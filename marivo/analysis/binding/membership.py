"""Source-free member path selection for an occurrence."""

from marivo._definition.deferred import RelationshipParameter
from marivo._definition.relations import MembershipBinding
from marivo._definition.types import ExactRef, FieldRead
from marivo.analysis.core.model import reject


def membership_path(
    binding: MembershipBinding, root: ExactRef
) -> tuple[RelationshipParameter, ...]:
    """Prefer an explicit directed allocation, then permit identity if applicable."""
    candidates = tuple(path for path in binding.paths if path[0].from_entity.ref == root.ref)
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
        return ()
    reject(
        "an explicit membership path or compatible member identity",
        root.ref.key,
        "Add a directed path from this contribution Entity to the member Entity.",
        "membership.bind",
    )


def membership_reads(binding: MembershipBinding, root: ExactRef) -> tuple[FieldRead, ...]:
    path = membership_path(binding, root)
    return tuple(
        FieldRead(field.field, field.entity, tuple(step.ref for step in path), field.at)
        for field in binding.fields
    )
