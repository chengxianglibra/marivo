from __future__ import annotations

from copy import deepcopy
from typing import Any

from .types import Compatibility, RequiredAction, RevisionClassificationResult, RevisionDiffEntry

_BREAKING_PATHS = {
    "header.metric_ref",
    "header.metric_family",
    "header.value_semantics",
    "header.observed_entity_ref",
    "header.population_subject_ref",
    "header.primary_time_ref",
    "header.observation_grain_ref",
    "header.additivity_constraints",
    "header.default_predicate_refs",
    "payload.metric_family",
    "payload.required_inputs",
}


_COMPATIBLE_PATHS = {
    "header.display_name",
    "header.description",
    "header.owner",
    "header.tags",
    "payload.unit.display_label",
}


_PARENT_DIFF_PATHS = {
    "header.additivity_constraints",
    "header.default_predicate_refs",
    "payload.required_inputs",
    "header.tags",
}


def classify_metric_revision(
    base: dict[str, Any],
    replacement: dict[str, Any],
) -> RevisionClassificationResult:
    canonical_base = _normalize(deepcopy(base))
    canonical_replacement = _normalize(deepcopy(replacement))
    base_flat = _flatten(canonical_base)
    replacement_flat = _flatten(canonical_replacement)
    paths = sorted(set(base_flat) | set(replacement_flat))
    diffs: list[RevisionDiffEntry] = []

    for path in paths:
        if base_flat.get(path) == replacement_flat.get(path):
            continue
        canonical_path = _canonical_diff_path(path)
        compatibility: Compatibility = (
            "compatible" if canonical_path in _COMPATIBLE_PATHS else "breaking"
        )
        diffs.append(
            RevisionDiffEntry(
                path=canonical_path,
                change_type=_change_type(canonical_path, compatibility),
                compatibility=compatibility,
                reason=_reason(canonical_path, compatibility),
            )
        )

    deduped = _dedupe_diffs(diffs)
    classified: Compatibility = (
        "breaking" if any(diff.compatibility == "breaking" for diff in deduped) else "compatible"
    )
    required_actions = (
        [_generic_breaking_revision_action(replacement)] if classified == "breaking" else []
    )
    return RevisionClassificationResult(
        classified_compatibility=classified,
        diff_summary=deduped,
        affected_dependents=[],
        required_actions=required_actions,
    )


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return sorted(value)
        return [_normalize(item) for item in value]
    return value


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        if not value:
            return {prefix: {}}
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            flattened.update(_flatten(child, child_prefix))
        return flattened
    if isinstance(value, list):
        if not value:
            return {prefix: []}
        flattened = {}
        for index, child in enumerate(value):
            flattened.update(_flatten(child, f"{prefix}[{index}]"))
        return flattened
    return {prefix: value}


def _canonical_diff_path(path: str) -> str:
    for parent in sorted(_PARENT_DIFF_PATHS, key=len, reverse=True):
        if path == parent or path.startswith(f"{parent}.") or path.startswith(f"{parent}["):
            return parent
    return path


def _dedupe_diffs(diffs: list[RevisionDiffEntry]) -> list[RevisionDiffEntry]:
    deduped: list[RevisionDiffEntry] = []
    seen: set[tuple[str, str]] = set()
    for diff in diffs:
        key = (diff.path, diff.compatibility)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(diff)
    return deduped


def _change_type(path: str, compatibility: Compatibility) -> str:
    if compatibility == "compatible":
        return "display_metadata"
    if path == "payload.required_inputs":
        return "metric_input_contract_changed"
    return "semantic_contract_changed"


def _reason(path: str, compatibility: Compatibility) -> str:
    if compatibility == "compatible":
        return "Display or metadata change does not alter runtime semantics."
    if path == "payload.required_inputs":
        return "Required metric inputs must be covered before activation."
    return "Semantic contract change requires dependency validation before activation."


def _generic_breaking_revision_action(replacement: dict[str, Any]) -> RequiredAction:
    metric_ref = str((replacement.get("header") or {}).get("metric_ref") or "")
    return RequiredAction(
        action_id="act_metric_revision_dependency_plan",
        action="resolve_breaking_revision_plan",
        target_ref=metric_ref,
        target_revision=None,
        depends_on=[],
        blocking=True,
        action_status="pending",
        completion_criteria={
            "kind": "breaking_metric_revision_plan_resolved",
            "expected": {
                "metric_ref": metric_ref,
            },
            "observed": None,
        },
        reason=(
            "Breaking metric revision requires dependency planning before activation; "
            "later tasks will replace this generic blocker with concrete dependent actions."
        ),
    )
