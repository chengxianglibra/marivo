"""Source-free directed relationship binding for scalar reads and observations."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.observation.route_inputs import RelationshipPath, RouteInput
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EntityKind, Ref, RelationshipKind, ref
from marivo.semantic.ir import TargetRelationshipContract
from marivo.semantic.metric_graph import TargetMetricContract
from marivo.semantic.validator import Registry, normalize_target_relationship

Path = tuple[Ref[RelationshipKind], ...]


def binding_error(
    expected: str,
    received: str,
    *,
    target: str,
    candidates: tuple[Path, ...] = (),
    root: str | None = None,
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
                "    mv.path("
                + ", ".join("ms.ref.relationship(" + repr(item.path) + ")" for item in path)
                + "),"
                for path in candidates
            )
            + "\n)"
            if candidates and root is not None
            else None,
        ),
    )


@dataclass(slots=True)
class RelationshipResolver:
    """One invocation's immutable mapping facts; no schema access or global cache."""

    registry: Registry
    mappings: tuple[TargetRelationshipContract, ...]
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
        return cls(registry, mappings, outgoing, {m.ref.path: m for m in mappings})

    def candidates(
        self,
        start: str,
        end: str,
        *,
        prefix: Path = (),
        bound_member: tuple[str, Path] | None = None,
    ) -> tuple[Path, ...]:
        if start == end:
            return ((),) if not prefix else ()
        found: list[Path] = []

        def walk(current: str, visited: frozenset[str], path: Path) -> None:
            if len(found) == 2:
                return
            for mapping in self.outgoing.get(current, ()):
                destination = mapping.to_entity_ref.path
                candidate = (*path, ref.relationship(mapping.ref.path))
                if (
                    destination in visited
                    or candidate[: min(len(candidate), len(prefix))] != prefix[: len(candidate)]
                ):
                    continue
                if (
                    bound_member is not None
                    and destination == bound_member[0]
                    and candidate != bound_member[1]
                ):
                    continue
                if destination == end:
                    if len(candidate) >= len(prefix):
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
        prefix: Path = (),
        target: str = "dsl.LogicalAnalysisDomain.observe",
        bound_member: tuple[str, Path] | None = None,
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
            if current != end or explicit[: len(prefix)] != prefix:
                raise binding_error(
                    "a path to the exact owner respecting declared roles",
                    f"{start} -> {current}; expected {end}",
                    target=target,
                )
            return explicit
        paths = self.candidates(start, end, prefix=prefix, bound_member=bound_member)
        if len(paths) != 1:
            raise binding_error(
                f"one unique directed to-one path from {start} to {end}",
                "multiple relationship roles"
                if paths
                else "no governed path satisfying the declared roles",
                target=target,
                candidates=paths,
                root=start,
            )
        return paths[0]

    def metric_paths(
        self,
        metric: TargetMetricContract,
        member: str,
        overrides: dict[str, Path],
        *,
        target: str = "dsl.LogicalAnalysisDomain.observe",
    ) -> tuple[Path, ...]:
        """Resolve canonical root bindings while preserving each declared time role."""
        paths: list[Path] = []
        for root in metric.computation_roots:
            prefixes = tuple(
                tuple(ref.relationship(item.path) for item in component.event_time_path)
                for component in metric.components
                if component.computation_root.path == root.path
            )
            prefix = max(prefixes, key=len, default=())
            if any(prefix[: len(item)] != item for item in prefixes):
                raise binding_error(
                    "compatible declared component roles",
                    root.path,
                    target=target,
                )
            paths.append(
                self.resolve(
                    root.path,
                    member,
                    explicit=overrides.get(root.path),
                    prefix=prefix,
                    target=target,
                )
            )
        return tuple(paths)

    def overrides(
        self, via: RouteInput, *, roots: tuple[Ref[EntityKind], ...], target: str
    ) -> dict[str, Path]:
        declared = via if isinstance(via, tuple) else (via,) if via is not None else ()
        if isinstance(via, tuple) and not via:
            raise binding_error("a nonempty tuple of paths", "empty tuple", target=target)
        result: dict[str, Path] = {}
        allowed = {root.path for root in roots}
        for item in declared:
            if isinstance(item, RelationshipPath):
                path = item.through
            elif isinstance(item, Ref) and item.kind == "relationship":
                path = (item,)
            else:
                raise binding_error(
                    "a Relationship Ref or RelationshipPath", repr(item), target=target
                )
            if path[0].path not in self.by_ref:
                raise binding_error("a loaded Relationship Ref", path[0].path, target=target)
            root = self.by_ref[path[0].path].from_entity_ref.path
            if root not in allowed or root in result:
                raise binding_error(
                    "one override for each of the distinct contribution roots", root, target=target
                )
            result[root] = path
        return result
