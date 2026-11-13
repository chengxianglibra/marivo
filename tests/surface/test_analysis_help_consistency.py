"""Analysis Help agrees with real symbolic, numeric and temporal boundaries."""

from datetime import timedelta

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.analysis._capabilities.dataset_model import CallableInput
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.core.data_types import DurationDataType
from marivo.analysis.errors import AnalysisError
from marivo.semantic import Entity
from tests.shared_fixtures import (
    DslCaseFactory,
    catalog_value,
)


@pytest.mark.parametrize(
    ("target", "output"),
    (
        ("identity", "Grouping | Deferred"),
        ("coordinates", "Population | Deferred"),
        ("membership", "Grouping | Deferred"),
        ("product", "Population | Deferred"),
        *(
            ("population." + method, "Population | Deferred")
            for method in (
                "project",
                "classify",
                "union",
                "intersection",
                "difference",
                "where",
                "image",
                "preimage",
            )
        ),
    ),
)
def test_symbolic_return_help_matches_callable_annotation(target: str, output: str) -> None:
    descriptor = REGISTRY.by_canonical_id(target)
    assert isinstance(descriptor, CallableInput)
    for binding in descriptor.bindings:
        assert str(binding.signature.return_annotation) == output
        assert callable(binding.implementation)


def test_symbolic_population_branches_remain_deferred(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    customer_entity = catalog_value(case.catalog, "sales.customer", kind="entity")
    order_entity = catalog_value(case.catalog, "sales.order", kind="entity")
    assert isinstance(customer_entity, Entity) and isinstance(order_entity, Entity)
    customers = case.session.members(customer_entity)
    orders = case.session.members(order_entity)
    region = ms.ref.dimension("sales.customer.region")
    customer = ms.ref.entity("sales.customer")
    buyer = ms.ref.relationship("sales.buyer")
    symbolic = mv.coordinates(by=(region,), keys=(("east",),))
    identities = mv.coordinates(by=(mv.identity(customer),), keys=((("A",),),))
    for value in (
        mv.identity(customer),
        symbolic,
        mv.membership(region, via=((buyer,),), unmatched="exclude"),
        mv.product(symbolic, identities),
        customers.project(by=(region,)),
        customers.classify(by=(region,)),
        customers.union(identities),
        customers.intersection(identities),
        customers.difference(identities),
        customers.where(mv.in_population(identities)),
        orders.image(via=(buyer,)),
        orders.preimage(identities, via=(buyer,)),
    ):
        assert isinstance(value, Deferred)


def test_operator_help_preserves_duration_units_and_boolean_null_rules(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = analysis_dsl_case_factory("j2").session
    frame = session.values(
        by=(),
        columns={"elapsed": "s", "usd": "USD", "eur": "EUR"},
        rows=(((), {"elapsed": timedelta(seconds=3), "usd": 3, "eur": 2}),),
    ).compute()
    frame = frame.select(
        {
            "elapsed": frame["elapsed"],
            "usd": frame["usd"],
            "eur": frame["eur"],
            "unknown": (frame["usd"] / 0) > 0,
            "false": False,
            "true": True,
        }
    ).compute()
    result = frame.select(
        {
            "duration": frame["elapsed"] / 2,
            "product": frame["usd"] * frame["eur"],
            "and": frame["unknown"] & frame["false"],
            "or": frame["unknown"] | frame["true"],
        }
    ).compute()
    rows = result.to_pandas()
    assert rows["duration"].tolist() == [timedelta(seconds=2)]
    dtype = result.schema.values["duration"].dtype
    assert isinstance(dtype, DurationDataType) and dtype.unit == "s"
    assert result.schema.values["product"].unit == "USD*EUR"
    assert rows["product"].tolist() == [6]
    assert rows["and"].tolist() == [False]
    assert rows["or"].tolist() == [True]


def test_read_help_requires_one_complete_identity_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    entity = catalog_value(case.catalog, "sales.customer", kind="entity")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    assert isinstance(entity, Entity) and isinstance(region, ms.Dimension)
    members = case.session.members(entity)
    classified = members.classify(by=(*members.axes, region))
    assert isinstance(classified, mv.Population)
    with pytest.raises(AnalysisError):
        case.session.read(classified, {"region": region})
    projected = classified.project(by=(classified.axes[0],))
    assert isinstance(projected, mv.Population)
    assert case.session.read(projected, {"region": region}).output_names == ("region",)


def test_read_help_example_reads_only_selected_members(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    descriptor = REGISTRY.by_canonical_id("session.read")
    assert isinstance(descriptor, CallableInput)
    namespace: dict[str, object] = {
        "session": case.session,
        "customer": case.catalog.get("entity:sales.customer"),
        "region": case.catalog.get("dimension:sales.customer.region"),
    }
    exec(compile(descriptor.example.code, "<session-read-help>", "exec"), namespace)
    result = namespace[descriptor.example.result]
    assert isinstance(result, mv.AnalysisFrame)
    assert result.to_pandas()["region"].tolist() == ["east", "east"]


def test_method_fact_help_keeps_details_with_their_owning_record() -> None:
    value = render_help_text("analysis.frame_method_value")[0]
    assert len(value) < 1500


def test_time_scope_by_help_example_executes(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    scope = case.session.time_scope(start="2026-08-01", end="2026-08-03")
    descriptor = REGISTRY.by_canonical_id("time_scope.by")
    assert isinstance(descriptor, CallableInput)
    namespace: dict[str, object] = {"scope": scope, "mv": mv}
    exec(compile(descriptor.example.code, "<time-scope-by-help>", "exec"), namespace)
    axis = namespace[descriptor.example.result]
    assert isinstance(axis, mv.CalendarAxis)
    revenue = case.session.catalog.get("metric:sales.revenue")
    assert revenue.kind == "metric"
    rows = case.session.observe(
        {"value": revenue}, by=mv.product(axis, mv.coordinates(by=(), keys=((),)))
    ).to_pandas(time_details=True)
    assert rows["calendar:day.label"].tolist() == ["2026-08-01", "2026-08-02"]
    assert rows["value"].tolist() == [450, 0]
