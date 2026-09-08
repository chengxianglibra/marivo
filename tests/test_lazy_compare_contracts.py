"""Pure private comparison admission, authority, and row continuation contracts."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    _KeyedCardinality,
    _row_contract_fingerprint,
    _StaticRowBound,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.analysis.operators.contracts import (
    ComparePayload,
    DeltaSemantics,
    WindowBucketAlignment,
    comparison_basis,
    decode_comparison_basis,
    window_bucket,
)
from marivo.analysis.operators.delta import LogicalDeltaDataset
from marivo.analysis.operators.registry import admit_local, implementation
from marivo.refs import ref
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_observation_fixtures import make_sources
from tests.lazy_retained_fixtures import setup_retained

REVENUE = ref.metric("sales.revenue")
REGION = ref.dimension("sales.customers.region")
DAY = ref.time_dimension("sales.orders.order_time")
JAN = time_scope(start="2026-01-01", end="2026-02-01")
FEB = time_scope(start="2026-02-01", end="2026-03-01")


def test_all_five_shapes_are_private_paired_delta_contracts_without_io() -> None:
    sources = make_sources()
    current, baseline = (
        sources.observe(REVENUE, time_scope=FEB),
        sources.observe(REVENUE, time_scope=JAN),
    )
    variants = (
        (current, baseline, "entity"),
        (current.aggregate(), baseline.aggregate(), "scalar"),
        (
            current.with_dimensions(REGION).aggregate(),
            baseline.with_dimensions(REGION).aggregate(),
            "dimension",
        ),
        (
            current.with_time_axis(DAY, grain=grain("day")).aggregate(),
            baseline.with_time_axis(DAY, grain=grain("day")).aggregate(),
            "time",
        ),
        (
            current.with_dimensions(REGION).with_time_axis(DAY, grain=grain("day")).aggregate(),
            baseline.with_dimensions(REGION).with_time_axis(DAY, grain=grain("day")).aggregate(),
            "dimension-time",
        ),
    )
    for left, right, shape in variants:
        delta = left.compare(right)
        assert isinstance(delta, LogicalDeltaDataset)
        assert str(delta.row_contract.shape_id) == f"delta/{shape}@v1"
        assert isinstance(delta.row_contract.family_semantics, DeltaSemantics)
        assert isinstance(delta._root, LogicalRootHandle)
        assert isinstance(delta._root.payload, ComparePayload)
        assert tuple(item.role for item in delta._root.inputs) == ("current", "baseline")
        assert delta._inputs[0] is left and delta._inputs[1] is right
        assert hasattr(delta, "attribute")
        assert not hasattr(delta, "discover")
        fields = {field.name: field for field in delta.schema.columns}
        assert fields["delta"].field_id.value == "generated.compare.delta@v1"
        if "time" in shape:
            assert fields["comparison_ordinal"].field_id in delta.row_contract.key_field_ids
            assert fields["current_time"].field_id not in delta.row_contract.key_field_ids
            assert fields["baseline_time"].field_id not in delta.row_contract.key_field_ids
        if shape != "entity":
            admit_local(delta, implementation(delta))
        else:
            with pytest.raises(DatasetConstructionError, match="source-required"):
                admit_local(delta, implementation(delta))


def test_only_metric_observation_windows_may_differ() -> None:
    sources = make_sources()
    current = sources.observe(REVENUE, time_scope=FEB)
    baseline = sources.observe(REVENUE, time_scope=JAN)
    current.where(gt(REVENUE, 2)).compare(baseline.where(gt(REVENUE, 2)))
    with pytest.raises(DatasetConstructionError, match="comparison scope"):
        current.where(gt(REVENUE, 2)).compare(baseline.where(gt(REVENUE, 3)))
    january_membership = sources.population(ref.entity("sales.orders"), time_scope=JAN)
    february_membership = sources.population(ref.entity("sales.orders"), time_scope=FEB)
    with pytest.raises(DatasetConstructionError, match="comparison scope"):
        sources.observe(REVENUE, population=january_membership).compare(
            sources.observe(REVENUE, population=february_membership)
        )
    sampled = sources.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=3, seed=1)
    )
    changed = sources.population(ref.entity("sales.orders")).sample(
        engine_sample(target_rows=4, seed=1)
    )
    with pytest.raises(DatasetConstructionError, match="comparison scope"):
        sources.observe(REVENUE, population=sampled).compare(
            sources.observe(REVENUE, population=changed)
        )


def test_metric_binding_and_shape_admission_teaches_exact_projection() -> None:
    sources = make_sources()
    values = sources.observe([REVENUE, ref.metric("sales.order_count")])
    with pytest.raises(DatasetConstructionError, match=r"dataset\.metric"):
        values.compare(values)
    values.metric(REVENUE).compare(values.metric(REVENUE))
    expanded = values.metric(REVENUE).with_dimensions(REGION)
    with pytest.raises(DatasetConstructionError, match="shapes"):
        expanded.compare(expanded)
    with pytest.raises(DatasetOwnershipError):
        sources.observe(REVENUE).compare(make_sources(session_id="foreign").observe(REVENUE))


def test_scope_and_sampling_definition_do_not_enter_delta_row_semantics() -> None:
    sources = make_sources()
    a = sources.observe(REVENUE, time_scope=JAN).aggregate()
    b = sources.observe(REVENUE, time_scope=FEB).aggregate()
    first, second = a.compare(b), b.compare(a)
    assert first.definition_fingerprint != second.definition_fingerprint
    assert _row_contract_fingerprint(first.row_contract) == _row_contract_fingerprint(
        second.row_contract
    )
    assert isinstance(first._root, LogicalRootHandle) and isinstance(
        first._root.payload, ComparePayload
    )
    assert (
        decode_comparison_basis(first._root.payload.spec.current_basis).observation_scope
        != decode_comparison_basis(first._root.payload.spec.baseline_basis).observation_scope
    )
    basis = decode_comparison_basis(comparison_basis(a))
    assert basis.membership_digest and basis.reference_axis == "sales.orders.order_time"
    with pytest.raises(DatasetConstructionError, match="invalid authority"):
        decode_comparison_basis('{"secret":"must-not-render"}')


@pytest.mark.parametrize("sampled_current", [False, True])
def test_compare_rejects_sampling_on_only_one_side(sampled_current: bool) -> None:
    sources = make_sources()
    population = sources.population(ref.entity("sales.orders"))
    exact = sources.observe(REVENUE, population=population).with_dimensions(REGION).aggregate()
    sampled = (
        sources.observe(REVENUE, population=population.sample(engine_sample(target_rows=3, seed=1)))
        .with_dimensions(REGION)
        .aggregate()
    )
    current, baseline = (sampled, exact) if sampled_current else (exact, sampled)
    with pytest.raises(DatasetConstructionError, match="incompatible comparison scope") as error:
        current.compare(baseline)
    assert error.value.expected == "same Population membership, sampling and non-time selection"


def test_delta_row_continuations_are_registered_and_scalar_rejected() -> None:
    metric = make_sources().observe(REVENUE).with_dimensions(REGION).aggregate()
    delta = metric.compare(metric)
    selected = delta.where(eq(delta.fields.get("coordinate_presence"), "matched"))
    ranked = selected.rank(selected.fields.get("delta"))
    limited = ranked.limit(2)
    assert isinstance(limited, LogicalDeltaDataset)
    assert isinstance(limited._root, LogicalRootHandle)
    assert limited._root.operator_id == "delta.limit"
    cardinality = limited.row_set_contract.cardinality
    assert isinstance(cardinality, _KeyedCardinality)
    assert isinstance(cardinality.row_bound, _StaticRowBound)
    assert cardinality.row_bound.max_rows == 2
    scalar = metric.rollup(drop_dimensions=(REGION,)).compare(
        metric.rollup(drop_dimensions=(REGION,))
    )
    calls: tuple[Callable[[], LogicalDeltaDataset], ...] = (
        lambda: scalar.where(gt(scalar.fields.get("delta"), 0)),
        lambda: scalar.rank(scalar.fields.get("delta")),
        lambda: scalar.limit(1),
    )
    for call in calls:
        with pytest.raises(DatasetConstructionError):
            call()
    semantics = delta.row_contract.family_semantics
    assert isinstance(semantics, DeltaSemantics)
    with pytest.raises(DatasetConstructionError):
        delta._registration.row_validator(
            replace(
                delta.row_contract,
                _token=_CORE_TOKEN,
                family_semantics=replace(semantics, _token=_CORE_TOKEN, numeric_type="string"),
            ),
            delta.row_set_contract,
        )


def test_compare_rejects_unregistered_alignment_even_with_the_same_discriminator() -> None:
    class UnregisteredAlignment(WindowBucketAlignment):
        pass

    metric = make_sources().observe(REVENUE).aggregate()
    accepted = metric.compare(metric, alignment=window_bucket())
    assert isinstance(accepted, LogicalDeltaDataset)
    alignment = UnregisteredAlignment()
    assert alignment.kind == "window_bucket"
    with pytest.raises(DatasetConstructionError, match="unsupported alignment policy") as error:
        metric.compare(metric, alignment=alignment)
    assert error.value.expected == "window_bucket() alignment"


@pytest.mark.runtime
def test_compare_rejects_cross_store_materialized_operands_in_both_directions(
    tmp_path: Path,
) -> None:
    first_project, second_project = tmp_path / "first", tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    first = setup_retained(first_project)
    second = setup_retained(second_project)
    first_logical = first.sources.observe(REVENUE).aggregate()
    second_logical = second.sources.observe(REVENUE).aggregate()
    first_retained = first_logical.execute()
    second_retained = second_logical.execute()
    assert first_retained._owner.store_id != second_retained._owner.store_id
    before = (snapshot(first.runtime), snapshot(second.runtime))
    first_queries = tuple(first.runtime.statistics.statements)
    second_queries = tuple(second.runtime.statistics.statements)
    calls: tuple[Callable[[], LogicalDeltaDataset], ...] = (
        lambda: first_logical.compare(second_retained),
        lambda: second_logical.compare(first_retained),
        lambda: first_retained.compare(second_logical),
        lambda: second_retained.compare(first_logical),
        lambda: first_retained.compare(second_retained),
        lambda: second_retained.compare(first_retained),
    )
    for call in calls:
        with pytest.raises(DatasetOwnershipError, match="foreign input authority") as error:
            call()
        assert error.value.expected == (
            "Logical inputs in the consuming Session; Materialized inputs in the same Store"
        )
    assert (snapshot(first.runtime), snapshot(second.runtime)) == before
    assert tuple(first.runtime.statistics.statements) == first_queries
    assert tuple(second.runtime.statistics.statements) == second_queries
