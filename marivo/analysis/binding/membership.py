"""Source-free member path selection for an occurrence."""

from dataclasses import replace

from marivo._definition.population_builders import membership_anchor
from marivo._definition.relations import MembershipBinding
from marivo._definition.types import ExactRef, FieldRead, PathParameter
from marivo.analysis.core.model import reject


def membership_path(binding: MembershipBinding, root: ExactRef) -> PathParameter | None:
    """Prefer an explicit directed allocation, then permit identity if applicable."""
    candidates = tuple(path for path in binding.paths if path.source.ref == root.ref)
    if len(candidates) > 1:
        reject(
            "one membership path for the contribution root",
            root.ref.key,
            "Remove competing explicit paths.",
            "membership.bind",
        )
    if candidates:
        return candidates[0]
    if all(field.entity.ref == root.ref for field in binding.fields):
        return None
    reject(
        "an explicit membership path or compatible member identity",
        root.ref.key,
        "Add a directed path from this contribution Entity to the member Entity.",
        "membership.bind",
    )


def membership_reads(binding: MembershipBinding, root: ExactRef) -> tuple[FieldRead, ...]:
    path = membership_path(binding, root)
    if path is None:
        return binding.fields
    return tuple(
        replace(
            field,
            path=tuple(step.ref for step in path.relationships),
            at=membership_anchor(binding.fields, path),
            source_at=path.source_at,
            step_ats=path.step_ats,
            path_root=path.source,
            path_facts=path.relationships,
        )
        for field in binding.fields
    )
