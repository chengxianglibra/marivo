"""Helpers for enforcing semantic-vs-canonical ref boundaries.

Semantic refs identify stable semantic-layer contracts such as explicit
``metric_ref`` / ``dimension_ref`` fields or ``semantic_ref`` bindings.

Canonical refs identify session-local evidence objects and provenance handles
such as ``finding_ref`` / ``proposition_ref`` / ``artifact_refs`` / ``step_ref``.
The two families may be associated, but explicit ref fields from one family
must not appear in the other's surface contracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.semantic_runtime.semantic_metadata import runtime_ref_kind

_SEMANTIC_REF_FIELD_NAMES = frozenset(
    {
        "semantic_ref",
        "metric_ref",
        "process_ref",
        "dimension_ref",
        "dimension_refs",
        "time_ref",
        "binding_ref",
        "binding_refs",
        "bound_object_ref",
        "source_object_ref",
        "output_semantics_ref",
        "subject_ref",
    }
)

_CANONICAL_REF_FIELD_NAMES = frozenset(
    {
        "finding_ref",
        "proposition_ref",
        "assessment_ref",
        "gap_ref",
        "artifact_ref",
        "artifact_refs",
        "step_ref",
        "source_step_refs",
        "source_artifact_lineages",
        "seed_finding_refs",
        "seed_entries",
        "assessment_dependencies",
        "supporting_finding_refs",
        "opposing_finding_refs",
        "blocking_gap_refs",
        "non_blocking_gap_refs",
        "applied_inference_record_refs",
    }
)

_CANONICAL_REF_SHAPES: tuple[frozenset[str], ...] = (
    frozenset({"session_id", "finding_id"}),
    frozenset({"session_id", "proposition_id"}),
    frozenset({"assessment_id", "proposition_id", "snapshot_seq"}),
    frozenset({"gap_id", "proposition_id"}),
    frozenset({"inference_record_id", "proposition_id", "assessment_id"}),
    frozenset({"artifact_id"}),
    frozenset({"session_id", "step_id", "step_type"}),
)


@dataclass(slots=True, frozen=True)
class RefBoundaryViolation:
    path: str
    reason: str
    value: str


class RefBoundaryError(ValueError):
    """Raised when a payload mixes semantic refs and canonical refs."""

    def __init__(self, surface: str, violations: Sequence[RefBoundaryViolation]) -> None:
        self.surface = surface
        self.violations = list(violations)
        first = self.violations[0]
        super().__init__(
            f"{surface} mixes semantic and canonical refs: {first.path} {first.reason} {first.value!r}"
        )


def is_semantic_ref(value: Any) -> bool:
    return isinstance(value, str) and runtime_ref_kind(value) is not None


def is_canonical_ref_mapping(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    key_set = frozenset(str(key) for key in value)
    return any(key_set == shape for shape in _CANONICAL_REF_SHAPES)


def assert_no_semantic_refs_in_canonical_payload(payload: Any, *, surface: str) -> None:
    violations = list(_find_semantic_refs_in_canonical_payload(payload, path=surface))
    if violations:
        raise RefBoundaryError(surface, violations)


def assert_no_canonical_refs_in_semantic_payload(payload: Any, *, surface: str) -> None:
    violations = list(_find_canonical_refs_in_semantic_payload(payload, path=surface))
    if violations:
        raise RefBoundaryError(surface, violations)


def _find_semantic_refs_in_canonical_payload(
    payload: Any,
    *,
    path: str,
) -> Sequence[RefBoundaryViolation]:
    violations: list[RefBoundaryViolation] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text in _SEMANTIC_REF_FIELD_NAMES:
                violations.append(
                    RefBoundaryViolation(
                        path=child_path,
                        reason="uses semantic ref field",
                        value=key_text,
                    )
                )
            violations.extend(_find_semantic_refs_in_canonical_payload(value, path=child_path))
        return violations
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, item in enumerate(payload):
            violations.extend(
                _find_semantic_refs_in_canonical_payload(item, path=f"{path}[{index}]")
            )
    return violations


def _find_canonical_refs_in_semantic_payload(
    payload: Any,
    *,
    path: str,
) -> Sequence[RefBoundaryViolation]:
    violations: list[RefBoundaryViolation] = []
    if isinstance(payload, Mapping):
        if is_canonical_ref_mapping(payload):
            violations.append(
                RefBoundaryViolation(
                    path=path,
                    reason="contains canonical ref object",
                    value="{" + ", ".join(sorted(str(key) for key in payload)) + "}",
                )
            )
        for key, value in payload.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text in _CANONICAL_REF_FIELD_NAMES:
                violations.append(
                    RefBoundaryViolation(
                        path=child_path,
                        reason="uses canonical ref field",
                        value=key_text,
                    )
                )
            violations.extend(_find_canonical_refs_in_semantic_payload(value, path=child_path))
        return violations
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, item in enumerate(payload):
            violations.extend(
                _find_canonical_refs_in_semantic_payload(item, path=f"{path}[{index}]")
            )
    return violations
