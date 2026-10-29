"""Source-free directed relationship binding for scalar reads and observations."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import Ref, RelationshipKind, ref
from marivo.semantic.ir import TargetRelationshipContract
from marivo.semantic.validator import Registry, normalize_target_relationship

Path = tuple[Ref[RelationshipKind], ...]


def binding_error(
    expected: str,
    received: str,
    *,
    target: str,
    candidates: tuple[Path, ...] = (),
) -> AnalysisError:
    choices = tuple(" -> ".join(item.path for item in path) for path in candidates)
    return AnalysisError(
        message="The governed relationship binding could not be resolved.",
        expected=expected,
        received=received,
        location="analysis.relationship_binding",
        repair=AnalysisRepair(
            kind="user_choice" if candidates else "inspect",
            action=(
                "Choose the intended relationship role with via, or pass an explicitly bound read to by."
                if candidates
                else "Inspect the exact endpoints and declare or select a directed to-one relationship path."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id=target),
            candidates=choices,
            snippet="choices = (\n"
            + "\n".join(
                "    ("
                + ", ".join(
                    "session.catalog.get(" + repr("relationship:" + item.path) + ")"
                    for item in path
                )
                + ",),"
                for path in candidates
            )
            + "\n)"
            if candidates
            else None,
        ),
    )


@dataclass(slots=True)
class RelationshipResolver:
    """One invocation's immutable mapping facts; no schema access or global cache."""

    registry: Registry
    outgoing: dict[str, tuple[TargetRelationshipContract, ...]]
    by_ref: dict[str, TargetRelationshipContract]

    @classmethod
    def build(cls, registry: Registry) -> RelationshipResolver:
        mappings = tuple(
            normalize_target_relationship(registry, name) for name in sorted(registry.relationships)
        )
        outgoing: dict[str, tuple[TargetRelationshipContract, ...]] = {}
        for mapping in mappings:
            if (
                mapping.cardinality in ("many_to_one", "one_to_one")
                and tuple(destination for _, destination in mapping.keys)
                == registry.entities[mapping.to_entity_ref.path].primary_key
            ):
                start = mapping.from_entity_ref.path
                outgoing[start] = (*outgoing.get(start, ()), mapping)
        return cls(registry, outgoing, {m.ref.path: m for m in mappings})

    def candidates(
        self,
        start: str,
        end: str,
    ) -> tuple[Path, ...]:
        if start == end:
            return ((),)
        found: list[Path] = []

        def walk(current: str, visited: frozenset[str], path: Path) -> None:
            if len(found) == 2:
                return
            for mapping in self.outgoing.get(current, ()):
                destination = mapping.to_entity_ref.path
                candidate = (*path, ref.relationship(mapping.ref.path))
                if destination in visited:
                    continue
                if destination == end:
                    found.append(candidate)
                else:
                    walk(destination, visited | {destination}, candidate)
                if len(found) == 2:
                    return

        walk(start, frozenset({start}), ())
        return tuple(found)

    def resolve(
        self,
        start: str,
        end: str,
        *,
        explicit: Path | None = None,
        target: str = "session.observe",
    ) -> Path:
        if explicit is not None:
            current = start
            visited = {start}
            for item in explicit:
                if item.path not in self.registry.relationships:
                    raise binding_error("a loaded Relationship Ref", item.path, target=target)
                mapping = self.by_ref[item.path]
                if mapping.from_entity_ref.path != current or mapping not in self.outgoing.get(
                    current, ()
                ):
                    raise binding_error(
                        "a contiguous directed to-one path", item.path, target=target
                    )
                current = mapping.to_entity_ref.path
                if current in visited:
                    raise binding_error("an acyclic directed path", item.path, target=target)
                visited.add(current)
            if current != end:
                raise binding_error(
                    "a path to the exact owner respecting declared roles",
                    f"{start} -> {current}; expected {end}",
                    target=target,
                )
            return explicit
        paths = self.candidates(start, end)
        if len(paths) != 1:
            raise binding_error(
                f"one unique directed to-one path from {start} to {end}",
                "multiple relationship roles"
                if paths
                else "no governed path satisfying the declared roles",
                target=target,
                candidates=paths,
            )
        return paths[0]
