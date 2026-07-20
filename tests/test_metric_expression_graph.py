"""Golden and boundary tests for the shared metric-expression graph contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

import pytest

from marivo.refs import Ref, RefPayloadV1
from marivo.semantic.metric_graph import (
    MAX_EXPRESSION_OCCURRENCES,
    CanonicalSliceEntryV1,
    CatalogBodyLeafV1,
    ExpressionOccurrenceV1,
    ExpressionPresentationV1,
    MetricExpressionGraphV1,
    MetricGraphNodeRecordV1,
    MetricGraphNodeV1,
    PresentationLabelV1,
    RatioNodeV1,
    RuntimeExpressionIdentity,
    RuntimeExpressionSubjectV1,
    SliceNodeV1,
)
from marivo.semantic.metric_graph_canonical import (
    MetricGraphContractError,
    canonical_bytes,
    canonical_value,
    canonicalize_slices,
    fingerprint,
    intern_nodes,
    metric_graph_from_bytes,
    metric_graph_from_value,
    node_fingerprint,
    validate_graph,
)


def _record(node):
    return MetricGraphNodeRecordV1(node_id=node_fingerprint(node), node=node)


def _metric_payload(path: str) -> RefPayloadV1:
    return RefPayloadV1.from_ref(Ref.metric(path))


def _dimension_payload(path: str) -> RefPayloadV1:
    return RefPayloadV1.from_ref(Ref.dimension(path))


def _ratio_graph() -> MetricExpressionGraphV1:
    numerator = _record(
        CatalogBodyLeafV1(
            kind="catalog_body_leaf",
            metric_ref=_metric_payload("sales.failed"),
            dependency_fingerprint="a" * 64,
        )
    )
    denominator = _record(
        CatalogBodyLeafV1(
            kind="catalog_body_leaf",
            metric_ref=_metric_payload("sales.requests"),
            dependency_fingerprint="b" * 64,
        )
    )
    ratio = _record(
        RatioNodeV1(
            kind="ratio",
            numerator_id=numerator.node_id,
            denominator_id=denominator.node_id,
            zero_division="null",
        )
    )
    return MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(ratio.node_id,),
        nodes=tuple(sorted((ratio, numerator, denominator), key=lambda item: item.node_id)),
        occurrences=(
            ExpressionOccurrenceV1(
                path="root[0]",
                node_id=ratio.node_id,
                child_paths=("root[0].numerator", "root[0].denominator"),
            ),
            ExpressionOccurrenceV1(path="root[0].numerator", node_id=numerator.node_id),
            ExpressionOccurrenceV1(path="root[0].denominator", node_id=denominator.node_id),
        ),
    )


def _slice_chain(depth: int) -> MetricExpressionGraphV1:
    leaf = _record(
        CatalogBodyLeafV1(
            kind="catalog_body_leaf",
            metric_ref=_metric_payload("sales.requests"),
            dependency_fingerprint="c" * 64,
        )
    )
    records = [leaf]
    node_id = leaf.node_id
    for index in range(depth - 1):
        record = _record(
            SliceNodeV1(
                kind="slice",
                child_id=node_id,
                predicates=(
                    CanonicalSliceEntryV1(
                        dimension_ref=_dimension_payload(f"sales.orders.axis_{index}"),
                        value="x",
                    ),
                ),
                predicate_dependencies=(
                    (_dimension_payload(f"sales.orders.axis_{index}"), "f" * 64),
                ),
            )
        )
        records.append(record)
        node_id = record.node_id

    occurrences = []
    for index, record in enumerate(reversed(records)):
        path = "root[0]" + ".child" * index
        child_paths = () if index == depth - 1 else (path + ".child",)
        occurrences.append(
            ExpressionOccurrenceV1(path=path, node_id=record.node_id, child_paths=child_paths)
        )
    return MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(node_id,),
        nodes=tuple(sorted(records, key=lambda item: item.node_id)),
        occurrences=tuple(occurrences),
    )


def _wrap_root_slice(
    graph: MetricExpressionGraphV1,
    *,
    value: str,
) -> MetricExpressionGraphV1:
    child_prefix = "root[0].child"
    remapped_occurrences = tuple(
        replace(
            occurrence,
            path=occurrence.path.replace("root[0]", child_prefix, 1),
            child_paths=tuple(
                child_path.replace("root[0]", child_prefix, 1)
                for child_path in occurrence.child_paths
            ),
        )
        for occurrence in graph.occurrences
    )
    node = SliceNodeV1(
        kind="slice",
        child_id=graph.roots[0],
        predicates=(
            CanonicalSliceEntryV1(
                dimension_ref=_dimension_payload("sales.orders.state"),
                value=value,
            ),
        ),
        predicate_dependencies=((_dimension_payload("sales.orders.state"), "9" * 64),),
    )
    record = _record(node)
    return MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(record.node_id,),
        nodes=intern_nodes((*(item.node for item in graph.nodes), node)),
        occurrences=(
            ExpressionOccurrenceV1(
                path="root[0]",
                node_id=record.node_id,
                child_paths=(child_prefix,),
            ),
            *remapped_occurrences,
        ),
    )


def test_canonical_bytes_and_fingerprint_are_stable() -> None:
    graph = _ratio_graph()
    validate_graph(graph)

    assert canonical_bytes(graph).startswith(b'{"schema":"metric-expression/v1","roots":[')
    assert fingerprint(graph) == "80b80582579b5a47fca14fd70aa1f8d5d3bac5b52dbbc4b486277013395b2abe"
    assert fingerprint(graph) == fingerprint(replace(graph))


def test_canonical_graph_bytes_round_trip_strictly() -> None:
    graph = _ratio_graph()

    assert metric_graph_from_bytes(canonical_bytes(graph)) == graph


def test_graph_decoder_rejects_unknown_fields_and_node_kinds() -> None:
    with_unknown_field = cast("dict[str, object]", canonical_value(_ratio_graph()))
    with_unknown_field["future"] = True
    with pytest.raises(MetricGraphContractError, match=r"unknown=\['future'\]"):
        metric_graph_from_value(with_unknown_field)

    with_unknown_node = cast("dict[str, object]", canonical_value(_ratio_graph()))
    node_records = cast("list[dict[str, object]]", with_unknown_node["nodes"])
    ratio_record = next(
        record
        for record in node_records
        if cast("dict[str, object]", record["node"])["kind"] == "ratio"
    )
    cast("dict[str, object]", ratio_record["node"])["kind"] = "future"
    with pytest.raises(MetricGraphContractError, match="unsupported metric graph node kind"):
        metric_graph_from_value(with_unknown_node)


def test_ratio_role_order_changes_identity() -> None:
    graph = _ratio_graph()
    root_record = next(record for record in graph.nodes if record.node_id == graph.roots[0])
    assert isinstance(root_record.node, RatioNodeV1)
    swapped = replace(
        root_record.node,
        numerator_id=root_record.node.denominator_id,
        denominator_id=root_record.node.numerator_id,
    )
    assert node_fingerprint(swapped) != root_record.node_id


@pytest.mark.parametrize("depth", [1, 10])
def test_depth_at_or_below_limit_is_accepted(depth: int) -> None:
    validate_graph(_slice_chain(depth))


def test_depth_above_limit_is_rejected() -> None:
    with pytest.raises(MetricGraphContractError, match="depth limit exceeded") as exc_info:
        validate_graph(_slice_chain(11))
    assert exc_info.value.kind == "depth_limit_exceeded"
    assert exc_info.value.observed_count == 11
    assert exc_info.value.limit == 10
    assert exc_info.value.path == "root[0]" + ".child" * 10


def test_pre_cse_occurrence_budget_cannot_be_bypassed_by_shared_node() -> None:
    leaf = _record(
        CatalogBodyLeafV1(
            kind="catalog_body_leaf",
            metric_ref=_metric_payload("sales.requests"),
            dependency_fingerprint="d" * 64,
        )
    )
    count = MAX_EXPRESSION_OCCURRENCES + 1
    graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(leaf.node_id,) * count,
        nodes=(leaf,),
        occurrences=tuple(
            ExpressionOccurrenceV1(path=f"root[{index}]", node_id=leaf.node_id)
            for index in range(count)
        ),
    )
    with pytest.raises(MetricGraphContractError, match="occurrence limit exceeded") as exc_info:
        validate_graph(graph)
    assert exc_info.value.kind == "occurrence_limit_exceeded"
    assert exc_info.value.observed_count == MAX_EXPRESSION_OCCURRENCES + 1
    assert exc_info.value.limit == MAX_EXPRESSION_OCCURRENCES
    assert exc_info.value.path == f"root[{MAX_EXPRESSION_OCCURRENCES}]"


def test_pre_cse_occurrence_budget_accepts_exact_limit() -> None:
    leaf = _record(
        CatalogBodyLeafV1(
            kind="catalog_body_leaf",
            metric_ref=_metric_payload("sales.requests"),
            dependency_fingerprint="e" * 64,
        )
    )
    graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(leaf.node_id,) * MAX_EXPRESSION_OCCURRENCES,
        nodes=(leaf,),
        occurrences=tuple(
            ExpressionOccurrenceV1(path=f"root[{index}]", node_id=leaf.node_id)
            for index in range(MAX_EXPRESSION_OCCURRENCES)
        ),
    )
    validate_graph(graph)


def test_intern_nodes_is_deterministic_and_deduplicates() -> None:
    graph = _ratio_graph()
    nodes = tuple(record.node for record in reversed(graph.nodes))

    assert intern_nodes((*nodes, nodes[0])) == graph.nodes


def test_presentation_labels_do_not_change_value_graph_identity() -> None:
    graph = _ratio_graph()
    first = ExpressionPresentationV1(
        schema="metric-presentation/v1",
        labels=(PresentationLabelV1(occurrence_path="root[0]", label="Failure rate"),),
    )
    second = replace(
        first,
        labels=(PresentationLabelV1(occurrence_path="root[0]", label="Error share"),),
    )

    assert fingerprint(first) != fingerprint(second)
    assert fingerprint(graph) == fingerprint(replace(graph))


def test_slice_above_ratio_canonicalizes_to_slices_at_both_leaves() -> None:
    submitted = _wrap_root_slice(_ratio_graph(), value="FAILED")

    first = canonicalize_slices(submitted)
    second = canonicalize_slices(first.graph, first.presentation)

    assert first.graph == second.graph
    assert first.presentation == second.presentation
    root = next(
        record.node for record in first.graph.nodes if record.node_id == first.graph.roots[0]
    )
    assert isinstance(root, RatioNodeV1)
    numerator = next(
        record.node for record in first.graph.nodes if record.node_id == root.numerator_id
    )
    denominator = next(
        record.node for record in first.graph.nodes if record.node_id == root.denominator_id
    )
    assert isinstance(numerator, SliceNodeV1)
    assert isinstance(denominator, SliceNodeV1)
    assert numerator.predicates == denominator.predicates
    assert fingerprint(first.graph) == fingerprint(second.graph)


def test_slice_label_moves_to_rewritten_root_and_descendant_labels_survive() -> None:
    submitted = _wrap_root_slice(_ratio_graph(), value="FAILED")
    presentation = ExpressionPresentationV1(
        schema="metric-presentation/v1",
        labels=(
            PresentationLabelV1(occurrence_path="root[0]", label="Failed share"),
            PresentationLabelV1(
                occurrence_path="root[0].child.numerator",
                label="Failed requests",
            ),
        ),
    )

    canonical = canonicalize_slices(submitted, presentation)

    assert canonical.presentation.labels == (
        PresentationLabelV1(occurrence_path="root[0]", label="Failed share"),
        PresentationLabelV1(
            occurrence_path="root[0].numerator.child",
            label="Failed requests",
        ),
    )


def test_unlabeled_slice_promotes_wrapped_child_root_label() -> None:
    submitted = _wrap_root_slice(_ratio_graph(), value="FAILED")
    presentation = ExpressionPresentationV1(
        schema="metric-presentation/v1",
        labels=(
            PresentationLabelV1(
                occurrence_path="root[0].child",
                label="Existing ratio label",
            ),
        ),
    )

    canonical = canonicalize_slices(submitted, presentation)

    assert canonical.presentation.labels == (
        PresentationLabelV1(occurrence_path="root[0]", label="Existing ratio label"),
    )


def test_conflicting_nested_slice_predicates_are_rejected() -> None:
    submitted = _wrap_root_slice(
        _wrap_root_slice(_ratio_graph(), value="FAILED"),
        value="SUCCEEDED",
    )

    with pytest.raises(MetricGraphContractError, match="slice predicate conflict"):
        canonicalize_slices(submitted)


def test_slice_rewrite_applies_depth_budget_after_canonicalization() -> None:
    submitted = _ratio_graph()
    for _ in range(11):
        submitted = _wrap_root_slice(submitted, value="FAILED")

    canonical = canonicalize_slices(submitted)

    validate_graph(canonical.graph)


def test_unknown_node_kind_is_rejected() -> None:
    @dataclass(frozen=True)
    class UnknownNode:
        kind: str = "future"

    unknown = cast("MetricGraphNodeV1", UnknownNode())
    record = MetricGraphNodeRecordV1(node_id=node_fingerprint(unknown), node=unknown)
    graph = MetricExpressionGraphV1(
        schema="metric-expression/v1",
        roots=(record.node_id,),
        nodes=(record,),
        occurrences=(ExpressionOccurrenceV1(path="root[0]", node_id=record.node_id),),
    )

    with pytest.raises(MetricGraphContractError, match="unsupported metric graph node"):
        validate_graph(graph)


def test_node_ids_are_content_addressed() -> None:
    graph = _ratio_graph()
    record = graph.nodes[0]
    corrupt = replace(record, node_id="0" * 64)
    broken = replace(
        graph, nodes=tuple(sorted((corrupt, *graph.nodes[1:]), key=lambda x: x.node_id))
    )
    with pytest.raises(MetricGraphContractError, match="node id mismatch"):
        validate_graph(broken)


def test_runtime_identity_is_session_independent_but_subject_is_not() -> None:
    expression_fingerprint = fingerprint(_ratio_graph())
    identity = RuntimeExpressionIdentity(
        kind="runtime_expression",
        expression_schema="metric-expression/v1",
        expression_fingerprint=expression_fingerprint,
    )
    first = RuntimeExpressionSubjectV1(
        kind="runtime_expression",
        session_id="session-a",
        expression_fingerprint=identity.expression_fingerprint,
        artifact_id="artifact-a",
        scope_fingerprint="scope",
    )
    second = replace(first, session_id="session-b", artifact_id="artifact-b")

    assert first.expression_fingerprint == second.expression_fingerprint
    assert fingerprint(first) != fingerprint(second)


def test_canonical_encoder_rejects_unknown_objects_and_non_finite_floats() -> None:
    with pytest.raises(MetricGraphContractError, match="unsupported canonical payload type"):
        canonical_bytes(object())
    with pytest.raises(MetricGraphContractError, match="non-finite"):
        canonical_bytes(float("inf"))
