"""Closed relationship paths, independent of execution and publication."""

from dataclasses import dataclass
from hashlib import sha256

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.refs import Ref, RelationshipKind, SemanticKind


@dataclass(frozen=True, slots=True, repr=False, init=False)
class RelationshipPath:
    """A nonempty ordered relationship composition constructed with mv.path()."""

    through: tuple[Ref[RelationshipKind], ...]

    def __init__(self) -> None:
        raise DatasetConstructionError(
            expected="a RelationshipPath constructed with mv.path()",
            received="direct RelationshipPath construction",
            repair="Use mv.path(first_relationship, *remaining_relationships).",
            location="analysis.relationship_binding",
            help_target="dsl.path",
        )

    @classmethod
    def _from_refs(cls, through: tuple[Ref[RelationshipKind], ...]) -> "RelationshipPath":
        result = object.__new__(cls)
        object.__setattr__(result, "through", through)
        result._validate()
        return result

    def _validate(self) -> None:
        if (
            type(self.through) is not tuple
            or not self.through
            or any(
                type(item) is not Ref or item.kind is not SemanticKind.RELATIONSHIP
                for item in self.through
            )
        ):
            raise DatasetConstructionError(
                expected="a nonempty ordered tuple of Relationship Refs",
                received=repr(self.through),
                repair="Use mv.path(first_relationship, *remaining_relationships).",
                location="analysis.relationship_binding",
                help_target="dsl.path",
            )

    def __repr__(self) -> str:
        identity = sha256(repr(self.through).encode()).hexdigest()[:12]
        return f"RelationshipPath(id={identity}, hops={len(self.through)}; .show())"

    def show(self) -> None:
        """Print this path's bounded ordered relationship references.

        Args: None.
        Returns: None.
        Example: ``mv.path(buyer).show()``.
        Constraints: Displays at most sixteen hops; never reads sources.
        """
        print(repr(self))
        for index, item in enumerate(self.through[:16], 1):
            print(f"  {index}: {item.path[:160]}")
        if len(self.through) > 16:
            print(f"  ... {len(self.through) - 16} more hops")


PathInput = Ref[RelationshipKind] | RelationshipPath
RouteInput = PathInput | tuple[PathInput, ...] | None
