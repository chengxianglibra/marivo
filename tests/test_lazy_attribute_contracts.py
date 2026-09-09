"""Definition-only admission and scoped Attribution identity contracts."""

from dataclasses import replace
from typing import Literal

import pytest

from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import DimensionInput
from marivo.analysis.observation.predicates import eq
from marivo.analysis.operators.attribute import attribute_method
from marivo.analysis.operators.attribution import LogicalAttributionDataset
from marivo.analysis.operators.attribution_contracts import (
    AttributePayload,
    AttributionSemantics,
    delta_part_authorities,
)
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources

REGION = ref.dimension("sales.customers.region")
CHANNEL = ref.dimension("sales.orders.channel")


@pytest.mark.parametrize("metric", ["revenue", "mean_amount"])
def test_retained_missing_axis_rejects_before_any_action(metric: str) -> None:
    from marivo.analysis.datasets.base import _make_materialized_dataset
    from marivo.analysis.datasets.descriptors import (
        _exact_byte_count,
        _make_schema,
        _resolved_type,
    )
    from marivo.analysis.datasets.state import _materialized_state
    from marivo.analysis.operators.delta import MaterializedDeltaDataset
    from marivo.analysis.refs import ArtifactRef

    # Trusted metadata exercises construction only; NoIoActionPort rejects execution.
    source = (
        make_sources().observe(ref.metric(f"sales.{metric}")).with_dimensions(REGION).aggregate()
    )
    logical = source.compare(source)
    ids = logical._registration.ids
    schema = _make_schema(
        tuple(
            replace(
                column,
                _token=_CORE_TOKEN,
                physical_type_state=_resolved_type(column.logical_type_id, ids=ids),
            )
            for column in logical.schema.columns
        )
    )
    state = _materialized_state(
        artifact_ref=ArtifactRef(ref="missing_axis"),
        artifact_session_ref=logical._owner.session_id,
        content_authority_digest="fixture-content",
        storage_kind_id="parquet",
        realized_schema=schema,
        realized_row_count=1,
        realized_byte_count=_exact_byte_count(64),
        producing_run_ref="fixture-run",
        quality_authority_digest="fixture-quality",
        evidence_authority_digest="fixture-evidence",
        ids=ids,
    )
    retained = _make_materialized_dataset(
        owner=logical._owner,
        registry=logical._registry,
        family_id=logical.kind,
        row_contract=logical.row_contract,
        row_set_contract=logical.row_set_contract,
        state=state,
        definition_fingerprint=logical.definition_fingerprint,
    )
    assert isinstance(retained, MaterializedDeltaDataset)
    with pytest.raises(
        DatasetConstructionError, match="materialized missing-axis barrier"
    ) as error:
        retained.attribute(axes=(REGION, CHANNEL))
    assert ".with_dimensions(*axes)" in str(error.value)


def test_joint_and_hierarchy_preserve_exact_axis_order_masks_and_scope() -> None:
    metric = (
        make_sources()
        .observe(ref.metric("sales.revenue"))
        .with_dimensions(REGION, CHANNEL)
        .aggregate()
    )
    delta = metric.compare(metric)
    joint = delta.attribute(axes=(CHANNEL,))
    assert isinstance(joint, LogicalAttributionDataset)
    semantics = joint.row_contract.family_semantics
    assert isinstance(semantics, AttributionSemantics)
    assert semantics.method == "additive_difference@v1"
    assert tuple(
        joint.fields.get(field.name).field_id
        for field in joint.schema.columns
        if field.role_id == "dimension"
    ) == (*semantics.scope_field_ids, *semantics.axis_field_ids)
    assert joint.fields.get("active_axis_mask").field_id not in joint.row_contract.key_field_ids
    hierarchy = delta.attribute(axes=(CHANNEL, REGION), mode="hierarchy", top_k=2)
    assert hierarchy.fields.get("active_axis_mask").field_id in hierarchy.row_contract.key_field_ids
    hierarchy.where(eq(hierarchy.fields.get("active_axis_mask"), (True, False))).rank(
        hierarchy.fields.get("contribution")
    ).limit(2)
    assert not hasattr(joint, "compare") and not hasattr(joint, "attribute")
    assert isinstance(joint._root, LogicalRootHandle) and isinstance(
        joint._root.payload, AttributePayload
    )


@pytest.mark.parametrize(
    "name,method",
    [
        ("revenue", "additive_difference@v1"),
        ("order_count", "additive_difference@v1"),
        ("mean_amount", "component_mix@v1"),
        ("weighted_amount", "component_mix@v1"),
        ("conversion_rate", "component_mix@v1"),
    ],
)
def test_exact_aggregation_selects_one_method(name: str, method: str) -> None:
    metric = make_sources().observe(ref.metric(f"sales.{name}")).with_dimensions(REGION).aggregate()
    result = metric.compare(metric).attribute(axes=(REGION,))
    semantics = result.row_contract.family_semantics
    assert isinstance(semantics, AttributionSemantics) and semantics.method == method


def test_invalid_axes_mode_topk_and_overlapping_partition_fail_before_io() -> None:
    metric = make_sources().observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    delta = metric.compare(metric)
    cases: list[tuple[tuple[DimensionInput, ...], Literal["joint", "hierarchy"], int | None]] = [
        ((), "joint", None),
        ((REGION, REGION), "joint", None),
        ((REGION,), "hierarchy", None),
        ((REGION,), "joint", True),
        ((REGION,), "joint", 1001),
    ]
    for axes, mode, top_k in cases:
        with pytest.raises(DatasetConstructionError):
            delta.attribute(axes=axes, mode=mode, top_k=top_k)
    authority = delta_part_authorities(delta.row_contract)[0][1]
    with pytest.raises(DatasetConstructionError, match="overlapping"):
        attribute_method(
            authority.model_copy(update={"axis_partitions": ((REGION.path, "overlapping"),)}),
            (REGION.path,),
        )
    component = authority.components[0]
    with pytest.raises(DatasetConstructionError, match="nonadditive"):
        attribute_method(
            authority.model_copy(
                update={"components": (component.model_copy(update={"spatial_merge": "blocked"}),)}
            ),
            (REGION.path,),
        )


def test_row_contract_rejects_inactive_axis_nullability_and_invalid_resolution() -> None:
    metric = make_sources().observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    result = metric.compare(metric).attribute(axes=(REGION,))
    semantics = result.row_contract.family_semantics
    assert isinstance(semantics, AttributionSemantics)
    with pytest.raises(DatasetConstructionError):
        result._registration.row_validator(
            replace(
                result.row_contract,
                _token=_CORE_TOKEN,
                family_semantics=replace(semantics, _token=_CORE_TOKEN, resolution_prefixes=()),
            ),
            result.row_set_contract,
        )


def test_sampling_class_is_row_meaning_but_sample_parameters_are_not() -> None:
    from marivo.analysis.datasets.descriptors import _row_contract_fingerprint
    from marivo.analysis.observation.sampling import engine_sample

    sources = make_sources()

    def build(target: int | None) -> LogicalAttributionDataset:
        population = sources.population(ref.entity("sales.orders"))
        if target is not None:
            population = population.sample(engine_sample(target_rows=target, seed=1))
        metric = (
            sources.observe(ref.metric("sales.revenue"), population=population)
            .with_dimensions(REGION)
            .aggregate()
        )
        return metric.compare(metric).attribute(axes=(REGION,))

    exact, first, second = build(None), build(2), build(3)
    assert isinstance(first.row_contract.family_semantics, AttributionSemantics)
    assert first.row_contract.family_semantics.approximation_class == "sampled_population"
    assert _row_contract_fingerprint(first.row_contract) == _row_contract_fingerprint(
        second.row_contract
    )
    assert _row_contract_fingerprint(exact.row_contract) != _row_contract_fingerprint(
        first.row_contract
    )
