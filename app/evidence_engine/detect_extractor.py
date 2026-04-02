"""detect artifact → anomaly_candidate finding extractor (Phase 4d-2).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1.

Artifact type: ``"anomaly_candidates"``   Schema version: ``"v1"``   Family: ``"detect"``

Maps each candidate item in the ``candidates`` list to one
:class:`AnomalyCandidateFinding`:

- ``candidates`` non-empty → 1 finding per candidate
- ``candidates`` empty     → 0 findings (success-empty, D4 allows for ``detect``)

Canonical item key
------------------
Uses ``window.start`` as the stable candidate key when available (each time
bucket appears at most once in the v1 single-series z-score scan).

For segment candidates (non-null ``slice``, no ``window``), the stable key is
derived from ``candidate.slice`` using the same percent-encoded ``k=v|k=v``
format as the observe extractor.  This ensures segment anomaly findings are
stable under re-extraction even if the artifact's candidate list order changes.

Falls back to the contract-backed canonical ``index`` (candidates are sorted by
score desc, deviation desc, window.start before being written to the artifact)
when neither ``window.start`` nor ``candidate.slice`` is available.

analysis_axis derivation
------------------------
- Candidate has ``window`` key (time-bucket anomaly)  → ``"time"``
- Candidate has non-null ``slice`` but no window       → ``"segment"``
- Otherwise                                            → ``"scalar"``

subject.slice derivation
------------------------
- For time-bucket and scalar candidates the artifact-level ``scope`` is used.
- For segment candidates the candidate's own ``slice`` keys are used (they
  identify *which segment* is anomalous); the artifact-level ``scope`` is
  intentionally excluded from ``subject.slice`` for segment findings so that
  the subject accurately reflects the atomic item boundary.

subject.grain derivation
------------------------
Extracted from ``artifact_payload["time_scope"]["grain"]`` when present.
This is non-null for v1 time-bucket detect artifacts (the detect runner
always embeds the scan grain in the artifact payload).

Artifact-embedded candidate_ref
--------------------------------
The v1 detect runner embeds a ``candidate_ref`` sub-object inside each
candidate dict (holding an index-based ``item_ref``).  The extractor
**ignores this field entirely** and reconstructs the canonical
``ArtifactItemRefRef`` from the stable key / index chosen by D2 priority
rules.  The runner-embedded ref uses a bare index and an ``artifact_id: null``
placeholder (filled post-insert); trusting it would produce an unstable or
null-artifact_id ref.
"""

from __future__ import annotations

from typing import Any

from app.evidence_engine.canonical_finding import (
    AnomalyCandidateFinding,
    AnomalyCandidatePayload,
    ArtifactItemRefRef,
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
# Helpers
# ---------------------------------------------------------------------------

_VALID_FLAG_LEVELS = frozenset({"high", "medium", "low"})
_VALID_GRAINS = frozenset({"hour", "day", "week", "month"})


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _escape_seg_component(s: str) -> str:
    """Percent-encode segment key separators to prevent key collisions.

    Uses the same encoding as observe_extractor._escape_seg_component:
    the stable segment key format is ``k=v|k=v``; values containing ``|``
    or ``=`` must be escaped to avoid collisions between distinct segments.
    """
    return s.replace("%", "%25").replace("|", "%7C").replace("=", "%3D")


def _segment_stable_key(slice_dict: dict[str, Any]) -> str:
    """Derive a stable key from a candidate's slice dict.

    Mirrors the observe extractor's segment key derivation: sorted dimension
    KV pairs joined by ``|``, with each component percent-encoded.
    """
    return "|".join(
        f"{_escape_seg_component(str(k))}={_escape_seg_component(str(v))}"
        for k, v in sorted(slice_dict.items())
    )


def _derive_analysis_axis(candidate: dict[str, Any]) -> str:
    """Choose FindingSubject.analysis_axis from the candidate's shape.

    Rules (artifact-finding-generation-rules.md § detect):
    - time-bucket candidate (has ``window``)        → ``"time"``
    - segment candidate (non-null ``slice``, no window) → ``"segment"``
    - scalar / unknown                               → ``"scalar"``
    """
    window = candidate.get("window")
    if isinstance(window, dict) and window:
        return "time"
    if candidate.get("slice") is not None:
        return "segment"
    return "scalar"


def _empty_quality() -> FindingQuality:
    return FindingQuality(
        data_complete=None,
        sample_size=None,
        row_count=None,
        null_rate=None,
        quality_status=None,
        quality_warnings=[],
    )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class DetectArtifactExtractor(FindingExtractor):
    """Extract :class:`AnomalyCandidateFinding`\\s from ``anomaly_candidates`` artifacts."""

    artifact_type = "anomaly_candidates"
    artifact_schema_version = "v1"
    family = "detect"
    extractor_name = "detect_artifact_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        metric: str | None = artifact_payload.get("metric")
        scope: dict[str, Any] = artifact_payload.get("scope") or {}
        candidates: list[dict[str, Any]] = artifact_payload.get("candidates") or []

        # Grain is embedded in time_scope by the detect runner for all v1
        # time-bucket artifacts (e.g. "day", "week").  Null when absent.
        time_scope: dict[str, Any] = artifact_payload.get("time_scope") or {}
        grain_raw: str | None = time_scope.get("grain")
        grain = grain_raw if grain_raw in _VALID_GRAINS else None

        findings: list[AnomalyCandidateFinding] = []
        for i, candidate in enumerate(candidates):
            window = candidate.get("window") or {}
            window_start: str = str(window.get("start", "")).strip()
            window_end: str = str(window.get("end", "")).strip()
            candidate_slice: dict[str, Any] | None = candidate.get("slice")

            analysis_axis = _derive_analysis_axis(candidate)

            # ── Stable canonical item key + item ref (D2 priority) ──────────
            # Priority 1: window.start (time-bucket candidates)
            # Priority 2: segment slice key (segment candidates)
            # Priority 3: contract-backed index (candidates sorted canonically)
            #
            # Note: the artifact also embeds a ``candidate_ref`` sub-object
            # inside each candidate dict (runner-side index-based ref with
            # ``artifact_id: null``).  We do NOT read that field — we always
            # reconstruct the canonical ref from the D2-priority key so that
            # the ref is stable and carries the correct artifact_id.
            if window_start:
                canonical_item_key, item_ref = make_item_identity("candidates", key=window_start)
            elif analysis_axis == "segment" and candidate_slice:
                stable_key = _segment_stable_key(candidate_slice)
                canonical_item_key, item_ref = make_item_identity("candidates", key=stable_key)
            else:
                canonical_item_key, item_ref = make_item_identity("candidates", index=i)

            finding_id = make_finding_id(artifact_id, "anomaly_candidate", canonical_item_key)

            # candidate_ref points back to this artifact item using the D2-stable ref.
            candidate_ref = ArtifactItemRefRef(
                artifact_id=artifact_id,
                item_ref=item_ref,
            )

            flag_raw = candidate.get("flag_level")
            flag_level = flag_raw if flag_raw in _VALID_FLAG_LEVELS else None

            observed_window = (
                {"kind": "range", "start": window_start, "end": window_end}
                if window_start and window_end
                else None
            )

            # subject.slice: for segment candidates use the candidate's own
            # slice keys (they identify *which segment* is anomalous).
            # For time-bucket and scalar candidates use the artifact-level scope.
            subject_slice: dict[str, Any] = (
                dict(candidate_slice)
                if analysis_axis == "segment" and candidate_slice
                else dict(scope)
            )

            provenance = FindingProvenance(
                source_step_type=step_ref["step_type"],
                extractor_name=self.extractor_name,
                extractor_version=self.extractor_version,
                artifact_schema_version=self.artifact_schema_version,
                canonical_item_key=canonical_item_key,
                artifact_item_ref=item_ref,
                projection_ref=None,
            )

            finding = AnomalyCandidateFinding(
                finding_id=finding_id,
                finding_type="anomaly_candidate",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice=subject_slice,
                    grain=grain,  # type: ignore[typeddict-item]
                    analysis_axis=analysis_axis,  # type: ignore[typeddict-item]
                ),
                observed_window=observed_window,  # type: ignore[typeddict-item]
                quality=_empty_quality(),
                provenance=provenance,
                payload=AnomalyCandidatePayload(
                    candidate_ref=candidate_ref,
                    score=_to_float_or_none(candidate.get("candidate_score")),
                    flag_level=flag_level,
                    actual_value=_to_float_or_none(candidate.get("observed_value")),
                    expected_value=_to_float_or_none(candidate.get("expected_value")),
                    deviation_absolute=_to_float_or_none(candidate.get("deviation_abs")),
                    deviation_relative=_to_float_or_none(candidate.get("deviation_pct")),
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
