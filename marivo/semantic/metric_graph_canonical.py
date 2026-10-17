"""Canonical encoding and validation for metric-expression graph contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import NoReturn

from marivo._definition import graph as _definition_graph
from marivo._definition.types import DefinitionGraphError
from marivo.refs import RefPayloadV1
from marivo.semantic.metric_graph import (
    MAX_EXPRESSION_DEPTH,
    MAX_EXPRESSION_OCCURRENCES,
    AggregateNodeV1,
    CanonicalSliceEntryV1,
    CanonicalValue,
    CatalogBodyLeafV1,
    CumulativeNodeV1,
    ExpressionOccurrenceV1,
    ExpressionPresentationV1,
    LinearNodeV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
    MetricGraphNodeV1,
    PresentationLabelV1,
    RatioNodeV1,
    SliceCanonicalizationV1,
    SliceNodeV1,
    WeightedMeanAggregateNodeV1,
    node_child_ids,
)


class MetricGraphContractError(ValueError):
    """Raised when a graph or canonical payload violates the v1 contract."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "graph_contract",
        observed_count: int | None = None,
        limit: int | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.observed_count = observed_count
        self.limit = limit
        self.path = path


def _invalid(
    message: str,
    *,
    kind: str = "graph_contract",
    observed_count: int | None = None,
    limit: int | None = None,
    path: str | None = None,
) -> NoReturn:
    raise MetricGraphContractError(
        message,
        kind=kind,
        observed_count=observed_count,
        limit=limit,
        path=path,
    )


def canonical_value(value: object) -> object:
    """Convert supported typed payloads to deterministic JSON values."""
    try:
        return _definition_graph.canonical_value(value)
    except DefinitionGraphError as exc:
        _invalid(str(exc))


def canonical_bytes(value: object) -> bytes:
    """Encode one supported payload as stable UTF-8 JSON bytes."""
    try:
        return _definition_graph.canonical_bytes(value)
    except DefinitionGraphError as exc:
        _invalid(str(exc))


def fingerprint(value: object) -> str:
    """Return the lowercase SHA-256 fingerprint of canonical bytes."""
    try:
        return _definition_graph.fingerprint(value)
    except DefinitionGraphError as exc:
        _invalid(str(exc))


def node_fingerprint(node: MetricGraphNodeV1) -> str:
    return fingerprint(node)


def intern_nodes(nodes: Iterable[MetricGraphNodeV1]) -> tuple[MetricGraphNodeRecordV1, ...]:
    """Content-address and deterministically deduplicate graph nodes."""
    records: dict[str, MetricGraphNodeRecordV1] = {}
    for node in nodes:
        node_id = node_fingerprint(node)
        candidate = MetricGraphNodeRecordV1(node_id=node_id, node=node)
        existing = records.get(node_id)
        if existing is not None and canonical_bytes(existing.node) != canonical_bytes(node):
            _invalid(f"metric graph node fingerprint collision at {node_id!r}")
        records[node_id] = candidate
    return tuple(records[node_id] for node_id in sorted(records))


def _validate_occurrence_tree(
    occurrence: ExpressionOccurrenceV1,
    *,
    occurrences: dict[str, ExpressionOccurrenceV1],
    node_children: dict[str, tuple[str, ...]],
    active_paths: set[str],
) -> tuple[int, str]:
    if occurrence.path in active_paths:
        _invalid(f"expression occurrence cycle detected at {occurrence.path!r}")
    expected_child_ids = node_children[occurrence.node_id]
    if len(expected_child_ids) != len(occurrence.child_paths):
        _invalid(
            f"occurrence {occurrence.path!r} has {len(occurrence.child_paths)} children; "
            f"node requires {len(expected_child_ids)}"
        )
    active_paths.add(occurrence.path)
    child_depths: list[tuple[int, str]] = []
    for child_path, expected_id in zip(occurrence.child_paths, expected_child_ids, strict=True):
        child = occurrences.get(child_path)
        if child is None:
            _invalid(f"occurrence {occurrence.path!r} references missing child path {child_path!r}")
        if child.node_id != expected_id:
            _invalid(
                f"occurrence {child_path!r} references node {child.node_id!r}; "
                f"expected {expected_id!r}"
            )
        child_depths.append(
            _validate_occurrence_tree(
                child,
                occurrences=occurrences,
                node_children=node_children,
                active_paths=active_paths,
            )
        )
    active_paths.remove(occurrence.path)
    if not child_depths:
        return 1, occurrence.path
    child_depth, deepest_path = max(child_depths, key=lambda item: (item[0], item[1]))
    return 1 + child_depth, deepest_path


def validate_graph(
    graph: MetricExpressionGraphV1,
    *,
    enforce_limits: bool = True,
) -> None:
    """Validate node ids, closure, ordered roots, cycles, depth, and budget."""
    if graph.schema != "metric-expression/v1":
        _invalid(f"unsupported metric expression schema: {graph.schema!r}")
    if not graph.roots:
        _invalid("metric expression graph requires at least one root")
    if enforce_limits and len(graph.occurrences) > MAX_EXPRESSION_OCCURRENCES:
        _invalid(
            f"expression occurrence limit exceeded: {len(graph.occurrences)} > "
            f"{MAX_EXPRESSION_OCCURRENCES}",
            kind="occurrence_limit_exceeded",
            observed_count=len(graph.occurrences),
            limit=MAX_EXPRESSION_OCCURRENCES,
            path=graph.occurrences[MAX_EXPRESSION_OCCURRENCES].path,
        )

    node_records = {record.node_id: record for record in graph.nodes}
    if len(node_records) != len(graph.nodes):
        _invalid("metric expression graph contains duplicate node ids")
    if tuple(sorted(node_records)) != tuple(record.node_id for record in graph.nodes):
        _invalid("metric expression graph nodes must be ordered by node id")

    node_children: dict[str, tuple[str, ...]] = {}
    for node_id, record in node_records.items():
        expected_id = node_fingerprint(record.node)
        if node_id != expected_id:
            _invalid(f"node id mismatch: got {node_id!r}, expected {expected_id!r}")
        try:
            children = node_child_ids(record.node)
        except TypeError as exc:
            _invalid(str(exc))
        missing = [child for child in children if child not in node_records]
        if missing:
            _invalid(f"node {node_id!r} references missing children {missing!r}")
        node_children[node_id] = children

    occurrences = {item.path: item for item in graph.occurrences}
    if len(occurrences) != len(graph.occurrences):
        _invalid("metric expression graph contains duplicate occurrence paths")
    root_paths = tuple(f"root[{index}]" for index in range(len(graph.roots)))
    reachable_paths: set[str] = set()

    def collect(path: str) -> None:
        if path in reachable_paths:
            return
        occurrence = occurrences[path]
        reachable_paths.add(path)
        for child_path in occurrence.child_paths:
            collect(child_path)

    for index, (root_id, root_path) in enumerate(zip(graph.roots, root_paths, strict=True)):
        root = occurrences.get(root_path)
        if root is None:
            _invalid(f"root {index} is missing occurrence path {root_path!r}")
        if root.node_id != root_id:
            _invalid(f"root {index} occurrence does not reference root node {root_id!r}")
        depth, deepest_path = _validate_occurrence_tree(
            root,
            occurrences=occurrences,
            node_children=node_children,
            active_paths=set(),
        )
        if enforce_limits and depth > MAX_EXPRESSION_DEPTH:
            _invalid(
                f"expression depth limit exceeded at {root_path!r}: "
                f"{depth} > {MAX_EXPRESSION_DEPTH}",
                kind="depth_limit_exceeded",
                observed_count=depth,
                limit=MAX_EXPRESSION_DEPTH,
                path=deepest_path,
            )
        collect(root_path)

    if reachable_paths != set(occurrences):
        unreachable = sorted(set(occurrences) - reachable_paths)
        _invalid(f"metric expression graph contains unreachable occurrences: {unreachable!r}")


def _canonical_child_paths(node: MetricGraphNodeV1, path: str) -> tuple[str, ...]:
    match node:
        case CatalogBodyLeafV1() | AggregateNodeV1() | WeightedMeanAggregateNodeV1():
            return ()
        case SliceNodeV1():
            return (f"{path}.child",)
        case CumulativeNodeV1():
            return (f"{path}.base",)
        case RatioNodeV1():
            return (f"{path}.numerator", f"{path}.denominator")
        case LinearNodeV1(terms=terms):
            return tuple(f"{path}.term[{index}]" for index in range(len(terms)))


def _with_child_ids(node: MetricGraphNodeV1, child_ids: tuple[str, ...]) -> MetricGraphNodeV1:
    match node:
        case CatalogBodyLeafV1() | AggregateNodeV1() | WeightedMeanAggregateNodeV1():
            if child_ids:
                _invalid(f"leaf node {node.kind!r} received child ids")
            return node
        case SliceNodeV1():
            return replace(node, child_id=child_ids[0])
        case CumulativeNodeV1():
            return replace(node, child_id=child_ids[0])
        case RatioNodeV1():
            return replace(
                node,
                numerator_id=child_ids[0],
                denominator_id=child_ids[1],
            )
        case LinearNodeV1(terms=terms):
            return replace(
                node,
                terms=tuple(
                    replace(term, child_id=child_id)
                    for term, child_id in zip(terms, child_ids, strict=True)
                ),
            )


def canonicalize_slices(
    graph: MetricExpressionGraphV1,
    presentation: ExpressionPresentationV1 | None = None,
) -> SliceCanonicalizationV1:
    """Push every slice to leaves and remap presentation occurrences."""
    validate_graph(graph, enforce_limits=False)
    resolved_presentation = presentation or ExpressionPresentationV1(
        schema="metric-presentation/v1",
        labels=(),
    )
    if resolved_presentation.schema != "metric-presentation/v1":
        _invalid(f"unsupported metric presentation schema: {resolved_presentation.schema!r}")
    input_nodes = {record.node_id: record.node for record in graph.nodes}
    input_occurrences = {occurrence.path: occurrence for occurrence in graph.occurrences}
    source_labels: dict[str, str] = {}
    for presentation_label in resolved_presentation.labels:
        if presentation_label.occurrence_path not in input_occurrences:
            _invalid(
                f"presentation label references unknown path {presentation_label.occurrence_path!r}"
            )
        if presentation_label.occurrence_path in source_labels:
            _invalid(
                f"presentation contains duplicate label path {presentation_label.occurrence_path!r}"
            )
        source_labels[presentation_label.occurrence_path] = presentation_label.label

    output_nodes: dict[str, MetricGraphNodeV1] = {}
    path_map: dict[str, str] = {}
    eliminated_slices: list[tuple[str, str, str]] = []

    def intern(node: MetricGraphNodeV1) -> str:
        node_id = node_fingerprint(node)
        output_nodes.setdefault(node_id, node)
        return node_id

    def merge_predicates(
        inherited: Mapping[RefPayloadV1, tuple[CanonicalValue, str]],
        node: SliceNodeV1,
        *,
        source_path: str,
    ) -> dict[RefPayloadV1, tuple[CanonicalValue, str]]:
        dependencies = dict(node.predicate_dependencies)
        if set(dependencies) != {item.dimension_ref for item in node.predicates}:
            _invalid(f"slice dependencies do not match predicates at {source_path!r}")
        merged = dict(inherited)
        for predicate in node.predicates:
            dimension_ref = predicate.dimension_ref
            value = predicate.value
            dependency_fingerprint = dependencies[dimension_ref]
            existing = merged.get(dimension_ref)
            if existing is not None:
                existing_value, existing_dependency = existing
                if canonical_bytes(existing_value) != canonical_bytes(value):
                    _invalid(
                        f"slice predicate conflict for {dimension_ref.path!r} at {source_path!r}: "
                        f"{existing_value!r} != {value!r}"
                    )
                if existing_dependency != dependency_fingerprint:
                    _invalid(
                        f"slice dependency conflict for {dimension_ref.path!r} at {source_path!r}"
                    )
            merged[dimension_ref] = (value, dependency_fingerprint)
        return merged

    def rewrite(
        source_path: str,
        canonical_path: str,
        inherited: Mapping[RefPayloadV1, tuple[CanonicalValue, str]],
    ) -> tuple[str, tuple[ExpressionOccurrenceV1, ...]]:
        occurrence = input_occurrences[source_path]
        node = input_nodes[occurrence.node_id]
        if isinstance(node, SliceNodeV1):
            child_path = occurrence.child_paths[0]
            path_map[source_path] = canonical_path
            eliminated_slices.append((source_path, child_path, canonical_path))
            return rewrite(
                child_path,
                canonical_path,
                merge_predicates(inherited, node, source_path=source_path),
            )

        input_child_paths = occurrence.child_paths
        if not input_child_paths:
            leaf_id = intern(node)
            if not inherited:
                path_map[source_path] = canonical_path
                return leaf_id, (ExpressionOccurrenceV1(path=canonical_path, node_id=leaf_id),)
            leaf_path = f"{canonical_path}.child"
            path_map[source_path] = leaf_path
            ordered = tuple(
                sorted(inherited.items(), key=lambda item: (item[0].kind.value, item[0].path))
            )
            slice_node = SliceNodeV1(
                kind="slice",
                child_id=leaf_id,
                predicates=tuple(
                    CanonicalSliceEntryV1(dimension_ref=dimension_ref, value=item[0])
                    for dimension_ref, item in ordered
                ),
                predicate_dependencies=tuple(
                    (dimension_ref, item[1]) for dimension_ref, item in ordered
                ),
            )
            slice_id = intern(slice_node)
            return slice_id, (
                ExpressionOccurrenceV1(
                    path=canonical_path,
                    node_id=slice_id,
                    child_paths=(leaf_path,),
                ),
                ExpressionOccurrenceV1(path=leaf_path, node_id=leaf_id),
            )

        path_map[source_path] = canonical_path
        canonical_child_paths = _canonical_child_paths(node, canonical_path)
        child_ids: list[str] = []
        child_occurrences: list[ExpressionOccurrenceV1] = []
        for input_child_path, canonical_child_path in zip(
            input_child_paths,
            canonical_child_paths,
            strict=True,
        ):
            child_id, rewritten_occurrences = rewrite(
                input_child_path,
                canonical_child_path,
                inherited,
            )
            child_ids.append(child_id)
            child_occurrences.extend(rewritten_occurrences)
        rewritten_node = _with_child_ids(node, tuple(child_ids))
        rewritten_id = intern(rewritten_node)
        return rewritten_id, (
            ExpressionOccurrenceV1(
                path=canonical_path,
                node_id=rewritten_id,
                child_paths=canonical_child_paths,
            ),
            *child_occurrences,
        )

    root_ids: list[str] = []
    output_occurrences: list[ExpressionOccurrenceV1] = []
    for index in range(len(graph.roots)):
        root_path = f"root[{index}]"
        root_id, root_occurrences = rewrite(root_path, root_path, {})
        root_ids.append(root_id)
        output_occurrences.extend(root_occurrences)
    canonical_graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=tuple(root_ids),
        nodes=intern_nodes(output_nodes.values()),
        occurrences=tuple(output_occurrences),
    )
    validate_graph(canonical_graph)

    def effective_root_label(source_path: str) -> tuple[str, str] | None:
        label = source_labels.get(source_path)
        if label is not None:
            return source_path, label
        occurrence = input_occurrences[source_path]
        if isinstance(input_nodes[occurrence.node_id], SliceNodeV1):
            return effective_root_label(occurrence.child_paths[0])
        return None

    canonical_labels: dict[str, str] = {}
    promoted_sources: set[str] = set()
    for wrapper_path, child_path, canonical_path in eliminated_slices:
        resolved = (
            (wrapper_path, source_labels[wrapper_path])
            if wrapper_path in source_labels
            else effective_root_label(child_path)
        )
        if resolved is None:
            continue
        source_path, display_label = resolved
        canonical_labels.setdefault(canonical_path, display_label)
        if source_path != wrapper_path:
            promoted_sources.add(source_path)
    for source_path, display_label in source_labels.items():
        if source_path in promoted_sources:
            continue
        canonical_labels.setdefault(path_map[source_path], display_label)
    canonical_presentation = ExpressionPresentationV1(
        schema="metric-presentation/v1",
        labels=tuple(
            PresentationLabelV1(occurrence_path=path, label=label)
            for path, label in sorted(canonical_labels.items())
        ),
    )
    return SliceCanonicalizationV1(
        graph=canonical_graph,
        presentation=canonical_presentation,
        occurrence_path_map=tuple(
            (occurrence.path, path_map[occurrence.path]) for occurrence in graph.occurrences
        ),
    )


__all__ = [
    "MetricGraphContractError",
    "canonical_bytes",
    "canonical_value",
    "canonicalize_slices",
    "fingerprint",
    "intern_nodes",
    "node_fingerprint",
    "validate_graph",
]
