"""Pure additive admission, expansion and exact common driver screening contracts."""

from dataclasses import replace

import pytest

import marivo.analysis as mv
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.base import Dataset, _make_materialized_dataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.datasets.state import _materialized_state
from marivo.analysis.observation.contracts import DimensionInput, owner_of
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.predicates import eq
from marivo.analysis.operators.candidate_dataset import (
    LogicalCandidateDataset,
    MaterializedCandidateDataset,
)
from marivo.analysis.operators.contracts import comparison_basis
from marivo.analysis.operators.delta import MaterializedDeltaDataset
from marivo.analysis.operators.driver_axes import validate_driver_candidate
from marivo.analysis.operators.driver_contracts import DriverCandidatePayload
from marivo.analysis.refs import ArtifactRef
from marivo.refs import ref
from tests.lazy_attribute_fixtures import CHANNEL, REGION
from tests.lazy_observation_fixtures import make_sources


def _retained(dataset: Dataset) -> Dataset:
    ids = dataset._registration.ids
    schema = d._make_schema(
        tuple(
            replace(
                f,
                _token=d._CORE_TOKEN,
                physical_type_state=d._resolved_type(f.logical_type_id, ids=ids),
            )
            for f in dataset.schema.columns
        )
    )
    state = _materialized_state(
        artifact_ref=ArtifactRef(ref="artifact_" + "a" * 32),
        artifact_session_ref=dataset._owner.session_id,
        content_authority_digest="fixture-content",
        storage_kind_id="parquet",
        realized_schema=schema,
        realized_row_count=1,
        realized_byte_count=d._exact_byte_count(64),
        producing_run_ref="fixture-run",
        quality_authority_digest="fixture-quality",
        evidence_authority_digest="fixture-evidence",
        ids=ids,
    )
    return _make_materialized_dataset(
        owner=replace(owner_of(dataset), comparison_basis_snapshot=comparison_basis(dataset))
        if dataset.kind == "metric"
        else dataset._owner,
        registry=dataset._registry,
        family_id=dataset.kind,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        state=state,
        definition_fingerprint=dataset.definition_fingerprint,
    )


@pytest.mark.parametrize("shape", ["scalar", "dimension", "time", "dimension-time", "entity"])
def test_each_delta_shape_builds_one_shared_scope_without_io(shape: str) -> None:
    metric = make_sources().observe(ref.metric("sales.revenue"))
    if "dimension" in shape:
        metric = metric.with_dimensions(REGION, CHANNEL)
    if "time" in shape:
        metric = metric.with_time_axis(
            ref.time_dimension("sales.orders.order_time"), grain=mv.grain("day")
        )
    if shape != "entity":
        metric = metric.aggregate()
    delta = metric.compare(metric)
    result = delta.discover.driver_axes(search_space=[REGION])
    assert isinstance(result, LogicalCandidateDataset)
    assert str(result.row_contract.shape_id) == "candidate/driver-axis@v1"
    assert isinstance(result._root, LogicalRootHandle)
    assert isinstance(result._root.payload, DriverCandidatePayload)
    spec = result._root.payload.spec
    assert spec.definition.search_space == (REGION.path,)
    assert not hasattr(spec.definition, "threshold")
    assert spec.definition.scope_fields == spec.scope_fields
    assert tuple(f.name for f in spec.scope_fields) == (
        *(("entity_identity",) if shape == "entity" else ()),
        *(("channel",) if "dimension" in shape else ()),
        *(("comparison_ordinal",) if "time" in shape else ()),
    )
    assert result.row_contract.key_field_ids == (
        *(f.field_id for f in spec.scope_fields),
        result.fields.get("axis_ref").field_id,
    )
    for f in spec.definition.paired_time_fields:
        assert f.field_id == delta.fields.get(f.name).field_id
        assert f.field_id not in result.row_contract.key_field_ids
    assert len(result._inputs) == (1 if "dimension" in shape else 3)
    assert "threshold" not in result.contract().render()
    for name in (
        "axis_ref",
        "axis_cardinality",
        "concentration_member_count",
        "concentration_share",
        "score",
    ):
        field = result.fields.get(name)
        value: str | int | float = REGION.path if name == "axis_ref" else 1
        result.where(eq(field, value))
    result.rank(result.fields.get("score")).limit(2)


@pytest.mark.parametrize("name", ["mean_amount", "weighted_amount", "conversion_rate"])
def test_component_mix_is_not_admitted(name: str) -> None:
    metric = make_sources().observe(ref.metric(f"sales.{name}")).with_dimensions(REGION).aggregate()
    with pytest.raises(DatasetConstructionError, match="component-mix"):
        metric.compare(metric).discover.driver_axes(search_space=[REGION])


@pytest.mark.parametrize("entity", [False, True])
def test_driver_scope_never_grants_population_input(entity: bool) -> None:
    sources = make_sources()
    metric = sources.observe(ref.metric("sales.revenue"))
    if not entity:
        metric = metric.aggregate()
    candidate = metric.compare(metric).discover.driver_axes(search_space=[REGION])
    retained = _retained(candidate)
    assert isinstance(retained, MaterializedCandidateDataset)
    for value in (candidate, retained):
        for selected in (value, value.rank(value.fields.get("score")).limit(1)):
            with pytest.raises(DatasetConstructionError, match="candidate/entity-outlier@v1"):
                sources.observe(ref.metric("sales.revenue"), population=selected)


@pytest.mark.parametrize("limit", [True, 0, -1, 1001])
def test_driver_invalid_limits_fail_before_io(limit: int) -> None:
    metric = make_sources().observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    with pytest.raises(DatasetConstructionError, match="limit"):
        metric.compare(metric).discover.driver_axes(search_space=[REGION], limit=limit)


@pytest.mark.parametrize(
    "axes", [[], [REGION, REGION], [ref.time_dimension("sales.orders.order_time")]]
)
def test_invalid_search_space(axes: list[DimensionInput]) -> None:
    metric = make_sources().observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    with pytest.raises(DatasetConstructionError):
        metric.compare(metric).discover.driver_axes(search_space=axes)


def test_search_space_order_uniqueness_and_closed_schema() -> None:
    metric = (
        make_sources()
        .observe(ref.metric("sales.revenue"))
        .with_dimensions(REGION, CHANNEL)
        .aggregate()
    )
    delta = metric.compare(metric)
    result = delta.discover.driver_axes(search_space=[REGION, CHANNEL])
    assert (
        result.definition_fingerprint
        == delta.discover.driver_axes(search_space=(REGION, CHANNEL)).definition_fingerprint
    )
    assert (
        result.definition_fingerprint
        != delta.discover.driver_axes(search_space=[CHANNEL, REGION]).definition_fingerprint
    )
    axis = result.fields.get("axis_ref")
    with pytest.raises(DatasetConstructionError):
        validate_driver_candidate(
            replace(result.row_contract, _token=d._CORE_TOKEN, key_field_ids=()),
            result.row_set_contract,
        )
    assert axis.field_id in result.row_contract.coordinate_field_ids


def test_materialized_delta_retained_axes_are_pure_and_missing_axis_is_a_barrier() -> None:
    metric = make_sources().observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    retained = _retained(metric.compare(metric))
    assert isinstance(retained, MaterializedDeltaDataset)
    candidate = retained.discover.driver_axes(search_space=[REGION])
    assert candidate._inputs == (retained,)
    assert isinstance(candidate._root, LogicalRootHandle) and isinstance(
        candidate._root.payload, DriverCandidatePayload
    )
    assert candidate._root.payload.spec.definition.input_state_kind == "materialized"
    with pytest.raises(
        DatasetConstructionError, match="materialized missing-axis barrier"
    ) as error:
        retained.discover.driver_axes(search_space=[REGION, CHANNEL])
    assert ".with_dimensions(*axes)" in str(error.value)


def test_logical_delta_expansion_stops_at_materialized_metric_operands() -> None:
    metric = make_sources().observe(ref.metric("sales.revenue")).with_dimensions(REGION).aggregate()
    retained = _retained(metric)
    assert isinstance(retained, MaterializedMetricDataset)
    for delta in (metric.compare(retained), retained.compare(metric), retained.compare(retained)):
        delta.discover.driver_axes(search_space=[REGION])
        with pytest.raises(DatasetConstructionError, match="retained Metric boundary"):
            delta.discover.driver_axes(search_space=[REGION, CHANNEL])
