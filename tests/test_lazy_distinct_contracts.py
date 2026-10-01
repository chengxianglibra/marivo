"""Definition-only exact membership admission, identity and source-only contracts."""

import traceback
from dataclasses import replace

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation import distinct_contracts
from marivo.analysis.observation.contracts import (
    MetricPayload,
)
from marivo.analysis.observation.distinct_contracts import (
    make_distinct_membership,
    membership_part_authorities,
    supported_distinct_key_type,
)
from marivo.analysis.observation.fold_contracts import (
    FoldAuthorityV1,
    MetricFoldAuthorityV1,
    decode_fold_authority,
)
from marivo.analysis.operators.attribute import attribute_method
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.errors import SemanticLoadError
from marivo.semantic.metric_graph import AggregateNodeV1
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from tests.lazy_distinct_fixtures import (
    CHANNEL,
    DISTINCT_BUYERS,
    make_distinct_registry,
    make_distinct_sources,
)


def test_governed_composite_identity_is_one_complete_nonnullable_key() -> None:
    metric = make_distinct_sources().observe(ref.metric("sales.distinct_composite"))
    authority = membership_part_authorities(metric.row_contract)[0][1]
    assert authority.membership is not None
    assert authority.membership.identity_signature == (
        ("tenant", "unknown"),
        ("id", "unknown"),
    )
    assert authority.membership.key_logical_type == "identity_tuple"
    assert authority.membership.source_column == ""


@pytest.mark.parametrize("name", ("distinct_web_buyers", "cumulative_distinct_buyers"))
def test_single_distinct_root_preserves_filter_and_cumulative_evaluation(name: str) -> None:
    metric = (
        make_distinct_sources()
        .observe(
            ref.metric(f"sales.{name}"),
            time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
        )
        .with_dimensions(CHANNEL)
        .with_time_axis(ref.time_dimension("sales.orders.order_time"), grain=grain("day"))
        .aggregate()
    )
    authority = membership_part_authorities(metric.row_contract)[0][1]
    assert authority.membership is not None
    assert authority.cumulative is (name == "cumulative_distinct_buyers")
    assert attribute_method(authority, (CHANNEL.path,)) == "distinct_membership@v1"
    assert isinstance(metric._root, LogicalRootHandle)
    assert isinstance(metric._root.payload, MetricPayload)
    assert metric._root.payload.definition.distinct_memberships == (authority.membership,)


def test_projection_keeps_only_the_selected_membership_authority() -> None:
    observed = make_distinct_sources().observe((DISTINCT_BUYERS, ref.metric("sales.revenue")))
    ordinary = observed.metric(ref.metric("sales.revenue"))
    distinct = observed.metric(DISTINCT_BUYERS)
    assert membership_part_authorities(ordinary.row_contract) == ()
    assert len(membership_part_authorities(distinct.row_contract)) == 1
    assert isinstance(ordinary._root, LogicalRootHandle)
    assert isinstance(ordinary._root.payload, MetricPayload)
    assert ordinary._root.payload.definition.distinct_memberships == ()


def test_status_time_fold_cannot_claim_exact_membership() -> None:
    registry, sidecar = make_distinct_registry()
    metric = normalize_target_metric(registry, DISTINCT_BUYERS.path, sidecar=sidecar)
    records = tuple(
        replace(record, node=replace(record.node, fold="last"))
        if isinstance(record.node, AggregateNodeV1)
        else record
        for record in metric.graph.nodes
    )
    folded = replace(metric, graph=replace(metric.graph, nodes=records))
    assert make_distinct_membership(folded, registry, sidecar) is None


@pytest.mark.parametrize(
    "logical_type",
    (
        "boolean",
        "int64",
        "uint64",
        "float64",
        "decimal(18, 3)",
        "string",
        "binary",
        "date",
        "time",
        "timestamp",
        "uuid",
    ),
)
def test_exact_scalar_key_types(logical_type: str) -> None:
    assert supported_distinct_key_type(logical_type)


@pytest.mark.parametrize(
    "logical_type", ("array<string>", "map<string, int64>", "struct<id: int64>", "null")
)
def test_nested_or_missing_scalar_key_types_are_not_membership_authority(logical_type: str) -> None:
    assert not supported_distinct_key_type(logical_type)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "sum"},
        {"kind": "count"},
        {"spatial_merge": "sum"},
        {"time_merge": "last"},
        {"empty_rule": "null"},
        {"null_rule": "non_null_pairs"},
        {"state_columns": ()},
    ],
)
def test_distinct_authority_rejects_incompatible_component_contract(
    change: dict[str, object],
) -> None:
    metric = make_distinct_sources().observe(DISTINCT_BUYERS).with_dimensions(CHANNEL).aggregate()
    authority = membership_part_authorities(metric.row_contract)[0][1]
    changed = authority.model_copy(
        update={"components": (authority.components[0].model_copy(update=change),)}
    )
    encoded = FoldAuthorityV1(schema_version=1, metrics=(changed,)).to_json()
    with pytest.raises(ValueError, match="distinct membership component"):
        decode_fold_authority(encoded)
    with pytest.raises(DatasetConstructionError) as caught:
        attribute_method(changed, (CHANNEL.path,))
    assert caught.value.received == "invalid exact distinct membership component"
    assert "count_distinct" in (caught.value.hint or "")
    assert "compatible engine target" in (caught.value.hint or "")
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("damage", ("root", "scalar_key"))
def test_distinct_attribution_reports_concrete_safe_authority_reason(damage: str) -> None:
    metric = make_distinct_sources().observe(DISTINCT_BUYERS).with_dimensions(CHANNEL).aggregate()
    authority = membership_part_authorities(metric.row_contract)[0][1]
    assert authority.membership is not None
    changed = (
        authority.model_copy(update={"root_id": "private-key-must-not-appear"})
        if damage == "root"
        else authority.model_copy(
            update={"membership": authority.membership.model_copy(update={"source_column": ""})}
        )
    )
    with pytest.raises(DatasetConstructionError) as caught:
        attribute_method(changed, (CHANNEL.path,))
    assert caught.value.received == (
        "invalid exact distinct membership root"
        if damage == "root"
        else "invalid scalar distinct membership key"
    )
    assert "private-key-must-not-appear" not in str(caught.value)


@pytest.mark.parametrize("extra_argument", (False, True))
def test_arbitrary_validation_error_values_and_causes_remain_private(
    monkeypatch: pytest.MonkeyPatch,
    extra_argument: bool,
) -> None:
    metric = make_distinct_sources().observe(DISTINCT_BUYERS).with_dimensions(CHANNEL).aggregate()
    authority = membership_part_authorities(metric.row_contract)[0][1]

    def fail(_authority: MetricFoldAuthorityV1) -> None:
        arguments = (
            ("invalid exact distinct membership component", "private-key-canary")
            if extra_argument
            else ("private-key-canary",)
        )
        raise ValueError(*arguments) from RuntimeError("private-key-canary")

    monkeypatch.setattr(distinct_contracts, "validate_membership_authority", fail)
    with pytest.raises(DatasetConstructionError) as caught:
        attribute_method(authority, (CHANNEL.path,))
    assert caught.value.received == "invalid membership authority"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "private-key-canary" not in "".join(traceback.format_exception(caught.value))
    assert "count_distinct" in (caught.value.hint or "")


@pytest.mark.parametrize("missing", ("sidecar", "body"))
def test_missing_distinct_source_facts_keep_the_semantic_typed_repair(missing: str) -> None:
    registry, sidecar = make_distinct_registry()
    metric = normalize_target_metric(registry, DISTINCT_BUYERS.path, sidecar=sidecar)
    target = ref.measure("sales.orders.buyer_key")
    broken: CompiledExpressionSidecar | None = None
    if missing == "body":
        broken = replace(
            sidecar, bodies={key: body for key, body in sidecar.bodies.items() if key != target}
        )
    with pytest.raises(SemanticLoadError) as caught:
        make_distinct_membership(metric, registry, broken)
    assert caught.value.received == "missing declared measure facts"
    assert caught.value.expected == "a loaded measure with declared expression type facts"
    assert caught.value.repair is not None


def test_computed_distinct_key_declines_membership_authority_without_error() -> None:
    registry, sidecar = make_distinct_registry()
    metric = normalize_target_metric(registry, DISTINCT_BUYERS.path, sidecar=sidecar)
    target = ref.measure("sales.orders.buyer_key")
    broken = replace(
        sidecar,
        bodies={**sidecar.bodies, target: replace(sidecar.bodies[target], source_column=None)},
    )
    # A computed measure has no single physical key column; membership authority
    # declines instead of raising, so the aggregate stays a typed derived fact.
    assert make_distinct_membership(metric, registry, broken) is None
