"""Closed relationship role selections, independent of execution and publication."""

from dataclasses import dataclass

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.refs import EntityKind, Ref, RelationshipKind, SemanticKind


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="exact distinct Entity roots and ordered Relationship Refs",
        received=received,
        repair="Bind mv.route(root, through=(...)) for each intended role; omit via for automatic binding.",
        location="analysis.relationship_binding",
        help_target="dsl.route",
    )


@dataclass(frozen=True, slots=True)
class RootRouteValue:
    root: Ref[EntityKind]
    through: tuple[Ref[RelationshipKind], ...]

    def __post_init__(self) -> None:
        if (
            type(self.root) is not Ref
            or self.root.kind is not SemanticKind.ENTITY
            or type(self.through) is not tuple
            or any(
                type(item) is not Ref or item.kind is not SemanticKind.RELATIONSHIP
                for item in self.through
            )
        ):
            raise _invalid("route requires an Entity and ordered Relationship Refs")


@dataclass(frozen=True, slots=True)
class RootRoutesValue:
    routes: tuple[RootRouteValue, ...]

    def __post_init__(self) -> None:
        if (
            type(self.routes) is not tuple
            or not self.routes
            or any(type(item) is not RootRouteValue for item in self.routes)
            or len({item.root for item in self.routes}) != len(self.routes)
        ):
            raise _invalid("one or more routes over distinct contribution roots")


def root_route(
    root: Ref[EntityKind], *, through: tuple[Ref[RelationshipKind], ...]
) -> RootRouteValue:
    return RootRouteValue(root, through)


def root_routes(*items: RootRouteValue) -> RootRoutesValue:
    return RootRoutesValue(items)
