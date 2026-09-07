"""Pure predicate normalization, exact row resolution and filter-order contracts."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetOwnershipError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import MetricPayload
from marivo.analysis.observation.predicates import all_of, eq, gt
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources

REVENUE = ref.metric("sales.revenue")
REGION = ref.dimension("sales.customers.region")
DAY = ref.time_dimension("sales.orders.order_time")


def test_equivalent_conjunctions_bind_one_canonical_tree_without_mutating_authoring() -> None:
    source = make_sources().observe(REVENUE).with_dimensions(REGION)
    a, b = gt(REVENUE, 1), eq(REGION, "PRIVATE_PREDICATE_4931")
    first = source.where(a, b)
    second = source.where(all_of(b, all_of(a, a)))
    assert first.definition_fingerprint == second.definition_fingerprint
    assert "PRIVATE_PREDICATE_4931" not in repr(b)
    assert "PRIVATE_PREDICATE_4931" not in first.contract().render()
    assert isinstance(first._root, LogicalRootHandle) and isinstance(
        first._root.payload, MetricPayload
    )
    assert "PRIVATE_PREDICATE_4931" not in repr(first._root.parameters)
    assert first.row_contract == source.row_contract
    assert b.operand is REGION


def test_membership_filter_requires_a_stable_unique_non_time_dimension() -> None:
    sources = make_sources()
    population = sources.population(ref.entity("sales.orders"))
    selected = population.where(eq(REGION, "EU"))
    assert selected.kind == "population"
    assert selected.row_contract == population.row_contract
    assert selected.definition_fingerprint != population.definition_fingerprint
    for predicate in (
        eq(DAY, date(2026, 2, 1)),
        gt(REVENUE, 1),
        eq(population.fields.get("entity_identity"), "opaque"),
    ):
        with pytest.raises(DatasetConstructionError):
            population.where(predicate)
    with pytest.raises(DatasetConstructionError):
        sources.population(ref.entity("sales.customers")).where(
            eq(ref.dimension("sales.orders.channel"), "web")
        )
    with pytest.raises(DatasetConstructionError):
        sources.population(
            ref.entity("sales.snapshots"),
            time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
        ).where(eq(REGION, "EU"))


def test_metric_fields_must_exist_at_current_rows() -> None:
    source = make_sources().observe(REVENUE)
    with pytest.raises(DatasetConstructionError):
        source.where(eq(REGION, "EU"))
    present = source.with_dimensions(REGION)
    assert present.where(eq(present.fields.dimension(REGION), "EU")).kind == "metric"
    foreign = make_sources(session_id="foreign").observe(REVENUE).fields.metric(REVENUE)
    with pytest.raises(DatasetOwnershipError):
        source.where(gt(foreign, 1))
    with pytest.raises(DatasetConstructionError):
        present.where(eq(REGION, "EU"), eq(REGION, "APAC"))


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), object(), [1], Decimal("NaN")])
def test_helpers_reject_non_scalar_or_non_finite_literals(value: object) -> None:
    with pytest.raises(DatasetConstructionError):
        eq(REVENUE, value)


@pytest.mark.parametrize("value", [True, "1", Decimal("1"), 2**64 + 1])
def test_float_binding_rejects_wrong_class_or_lossy_integer(value: object) -> None:
    with pytest.raises(DatasetConstructionError):
        make_sources().observe(REVENUE).where(gt(REVENUE, value))


def test_dates_and_string_ordering_have_no_implicit_coercion() -> None:
    source = (
        make_sources()
        .observe(REVENUE)
        .with_dimensions(REGION)
        .with_time_axis(DAY, grain=grain("day"))
    )
    assert source.where(eq(DAY, date(2026, 2, 1))).kind == "metric"
    for predicate in (
        eq(DAY, "2026-02-01"),
        eq(DAY, datetime(2026, 2, 1, tzinfo=timezone.utc)),
        gt(REGION, "EU"),
    ):
        with pytest.raises(DatasetConstructionError):
            source.where(predicate)


def test_filter_position_changes_semantic_definition() -> None:
    source = make_sources().observe(REVENUE).with_dimensions(REGION)
    before = source.where(gt(REVENUE, 60)).aggregate()
    after = source.aggregate().where(gt(REVENUE, 60))
    assert before.definition_fingerprint != after.definition_fingerprint
    assert isinstance(before._root, LogicalRootHandle) and isinstance(
        before._root.payload, MetricPayload
    )
    assert before._root.payload.definition.selection_boundaries
    assert isinstance(after._root, LogicalRootHandle) and isinstance(
        after._root.payload, MetricPayload
    )
    assert after._root.payload.definition.entity_present is False


def test_invalid_predicate_shapes_fail_locally() -> None:
    source = make_sources().observe(REVENUE)
    with pytest.raises(DatasetConstructionError):
        source.where()
    with pytest.raises(DatasetConstructionError):
        source.where(True)
    with pytest.raises(DatasetConstructionError):
        all_of(gt(REVENUE, 0))
    with pytest.raises(DatasetConstructionError):
        bool(eq(REVENUE, 0))


def test_provably_contradictory_ordering_fails_without_rows() -> None:
    source = make_sources().observe(REVENUE)
    with pytest.raises(DatasetConstructionError, match="contradictory"):
        source.where(eq(REVENUE, 1), gt(REVENUE, 2))
    assert source.where(eq(REVENUE, 2), gt(REVENUE, 1)).kind == "metric"
