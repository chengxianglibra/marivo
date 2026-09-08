"""Exact component demand at retained and source execution boundaries."""

from marivo.analysis.materialization.retained import metric_parts, required_part_roles
from marivo.analysis.observation.fold_contracts import fold_part_role
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.observation.rollup import retained_aggregate
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
REGION = ref.dimension("sales.customers.region")


def test_projection_demands_only_its_selected_metric_state() -> None:
    metric = make_sources().observe((REVENUE, MEAN))
    selected = metric.metric(REVENUE)
    expected = {fold_part_role(part) for part in metric_parts(selected.row_contract)}
    assert len(expected) == 1
    assert required_part_roles(selected, input_dataset=metric) == expected
    assert required_part_roles(selected.aggregate(), input_dataset=metric) == expected


def test_observation_consumes_metric_population_without_component_state() -> None:
    sources = make_sources()
    population = sources.observe(REVENUE)
    observed = sources.observe(MEAN, population=population)
    assert required_part_roles(observed, input_dataset=population) == set()
    assert required_part_roles(observed.aggregate(), input_dataset=population) == set()


def test_fold_consumed_before_projection_requires_all_its_input_states() -> None:
    metric = make_sources().observe((REVENUE, MEAN))
    folded = retained_aggregate(metric)
    assert isinstance(folded, LogicalMetricDataset)
    selected = folded.metric(REVENUE)
    expected = {fold_part_role(part) for part in metric_parts(metric.row_contract)}
    assert len(expected) == 2
    assert required_part_roles(selected, input_dataset=metric) == expected


def test_compare_demands_each_selected_operand_state_without_sibling_roles() -> None:
    sources = make_sources()
    current = sources.observe((REVENUE, MEAN))
    baseline = sources.observe((REVENUE, MEAN))
    selected = current.metric(REVENUE).aggregate()
    delta = selected.compare(baseline.metric(REVENUE).aggregate())
    expected = {fold_part_role(part) for part in metric_parts(selected.row_contract)}
    assert len(expected) == 1
    assert required_part_roles(delta, input_dataset=current) == expected
    assert required_part_roles(delta, input_dataset=baseline) == expected
    assert required_part_roles(delta, input_dataset=sources.observe(MEAN)) == set()


def test_attribution_demands_complete_delta_sides_through_result_continuations() -> None:
    metric = make_sources().observe(REVENUE).with_dimensions(REGION).aggregate()
    delta = metric.compare(metric)
    attributed = delta.attribute(axes=(REGION,))
    selected = attributed.rank(attributed.fields.get("contribution")).limit(1)
    selected_delta = delta.rank(delta.fields.get("delta")).limit(1)
    expected = {"delta_components.current", "delta_components.baseline"}
    assert required_part_roles(attributed, input_dataset=delta) == expected
    assert required_part_roles(selected, input_dataset=delta) == expected
    assert required_part_roles(selected_delta, input_dataset=delta) == expected
    assert required_part_roles(selected, input_dataset=attributed) == set()
