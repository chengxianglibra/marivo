from __future__ import annotations

from typing import Any

from .types import Compatibility, RequiredAction


def metric_revision_dependency_actions(
    metric_ref: str,
    metric_revision: int | None,
    classification: Compatibility,
    binding_refs: list[str],
    profile_refs: list[str],
    missing_metric_inputs_by_binding: dict[str, list[str]],
) -> list[RequiredAction]:
    if classification == "compatible":
        return []

    actions: list[RequiredAction] = []
    derive_action_ids: dict[str, str] = {}
    for binding_ref in binding_refs:
        action_id = f"act_metric_revision_{metric_revision or 'draft'}_derive_{_slug(binding_ref)}"
        derive_action_ids[binding_ref] = action_id
        actions.append(
            RequiredAction(
                action_id=action_id,
                action="derive_revision",
                target_ref=binding_ref,
                target_revision=None,
                depends_on=[],
                blocking=True,
                action_status="pending",
                completion_criteria={
                    "kind": "binding_revision_derived",
                    "expected": {
                        "metric_ref": metric_ref,
                        "metric_revision": metric_revision,
                        "binding_ref": binding_ref,
                    },
                    "observed": None,
                },
                reason="Published metric binding must derive a revision for this breaking metric revision.",
            )
        )

    for binding_ref in binding_refs:
        derive_action_id = derive_action_ids[binding_ref]
        for input_ref in missing_metric_inputs_by_binding.get(binding_ref, []):
            actions.append(
                RequiredAction(
                    action_id=(
                        f"act_metric_revision_{metric_revision or 'draft'}_"
                        f"coverage_{_slug(binding_ref)}_{_slug(input_ref)}"
                    ),
                    action="add_binding_coverage",
                    target_ref=binding_ref,
                    target_revision=None,
                    depends_on=[derive_action_id],
                    blocking=True,
                    action_status="pending",
                    completion_criteria={
                        "kind": "metric_input_binding_coverage_added",
                        "expected": {
                            "metric_ref": metric_ref,
                            "metric_revision": metric_revision,
                            "binding_ref": binding_ref,
                            "coverage_target": input_ref,
                        },
                        "observed": None,
                    },
                    reason=(
                        "New required metric input is not covered by the current published binding."
                    ),
                )
            )

    for profile_ref in profile_refs:
        actions.append(
            RequiredAction(
                action_id=(
                    f"act_metric_revision_{metric_revision or 'draft'}_revalidate_{_slug(profile_ref)}"
                ),
                action="reuse_after_revalidate",
                target_ref=profile_ref,
                target_revision=None,
                depends_on=[],
                blocking=True,
                action_status="pending",
                completion_criteria={
                    "kind": "compiler_profile_revalidated",
                    "expected": {
                        "metric_ref": metric_ref,
                        "metric_revision": metric_revision,
                        "profile_ref": profile_ref,
                    },
                    "observed": None,
                },
                reason="Published compiler compatibility profile must be revalidated before reuse.",
            )
        )
    return actions


def metric_revision_affected_dependents(
    binding_refs: list[str],
    profile_refs: list[str],
) -> list[dict[str, Any]]:
    return [{"kind": "binding", "ref": binding_ref} for binding_ref in binding_refs] + [
        {"kind": "compiler_profile", "ref": profile_ref} for profile_ref in profile_refs
    ]


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")
