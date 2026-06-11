"""Tests for the demand-driven semantic richness report."""

from __future__ import annotations

import json


def test_richness_report_to_dict_is_json_safe():
    from marivo.semantic.richness import RichnessGap, RichnessReport

    report = RichnessReport(
        gaps=(
            RichnessGap(
                kind="coverage",
                subkind="fact_table_no_metric",
                refs=("sales.orders",),
                demand_weight=3.0,
                demand_evidence=("run_history:sales.orders",),
                suggested_action="Declare a metric.",
            ),
        ),
        checked_at="2026-06-01T00:00:00Z",
    )

    payload = report.to_dict()
    assert payload["gaps"][0]["kind"] == "coverage"
    assert payload["gaps"][0]["refs"] == ["sales.orders"]
    assert payload["gaps"][0]["demand_weight"] == 3.0
    assert json.loads(json.dumps(payload))["checked_at"] == "2026-06-01T00:00:00Z"


_DEPTH_BARE = (
    "import marivo.semantic as ms\n"
    "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
    "@ms.dimension(entity=orders, name='amount')\n"
    "def amount(table):\n    return table.amount\n"
)

_DEPTH_ENRICHED = (
    "import marivo.semantic as ms\n"
    "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'),\n"
    "    ai_context={'business_definition': 'One row per order.',\n"
    "               'guardrails': ['Exclude test orders.'],\n"
    "               'synonyms': ['sales'], 'examples': ['how many orders?']})\n"
    "@ms.dimension(entity=orders, name='amount',\n"
    "    ai_context={'business_definition': 'Gross amount.',\n"
    "               'guardrails': ['USD only.'],\n"
    "               'synonyms': ['revenue'], 'examples': ['total amount?']})\n"
    "def amount(table):\n    return table.amount\n"
)


def _model(objects_src: str):
    return {
        "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
        "sales/objects.py": objects_src,
    }


def test_detect_depth_flags_empty_ai_context_slots(semantic_project_factory):
    from marivo.semantic.richness import _detect_depth

    project = semantic_project_factory(_model(_DEPTH_BARE))
    by_ref: dict[str, set[str]] = {}
    for subkind, refs in _detect_depth(project._registry):
        by_ref.setdefault(refs[0], set()).add(subkind)

    assert by_ref["sales.orders.amount"] == {
        "missing_business_definition",
        "missing_guardrails",
        "missing_synonyms",
        "missing_examples",
    }


def test_detect_depth_skips_enriched_objects(semantic_project_factory):
    from marivo.semantic.richness import _detect_depth

    project = semantic_project_factory(_model(_DEPTH_ENRICHED))
    flagged_refs = {refs[0] for _subkind, refs in _detect_depth(project._registry)}
    assert "sales.orders.amount" not in flagged_refs
    assert "sales.orders" not in flagged_refs


def test_demand_signal_defaults_are_empty():
    from marivo.semantic.richness import DemandSignal

    demand = DemandSignal()
    assert demand.example_questions == ()
    assert demand.intents == ()
    assert demand.run_history_refs == ()
    assert demand.build_purpose is None


_FACT_NO_METRIC = (
    "import marivo.semantic as ms\n"
    "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
    "@ms.dimension(entity=orders, name='amount')\n"
    "def amount(table):\n    return table.amount\n"
)

_FACT_WITH_METRIC = _FACT_NO_METRIC + (
    "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(),\n"
    "    verification_mode='python_native')\n"
    "def revenue(table):\n    return table.amount.sum()\n"
)

_SHARED_KEYS_NO_REL = (
    "import marivo.semantic as ms\n"
    "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['customer_id'], source=ms.table('orders'))\n"
    "customers = ms.entity(name='customers', datasource='warehouse', primary_key=['customer_id'], source=ms.table('customers'))\n"
)

_SHARED_KEYS_WITH_REL = (
    "import marivo.semantic as ms\n"
    "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['customer_id'], source=ms.table('orders'))\n"
    "@ms.dimension(entity=orders, name='order_customer')\n"
    "def order_customer(table):\n    return table.customer_id\n"
    "customers = ms.entity(name='customers', datasource='warehouse', primary_key=['customer_id'], source=ms.table('customers'))\n"
    "@ms.dimension(entity=customers, name='customer_pk')\n"
    "def customer_pk(table):\n    return table.customer_id\n"
    "ms.relationship(name='orders_to_customers', from_entity=orders,\n"
    "    to_entity=customers, from_dimensions=[order_customer], to_dimensions=[customer_pk])\n"
)


def _coverage_subkinds(project):
    from marivo.semantic.richness import _detect_coverage

    return {subkind for subkind, _refs in _detect_coverage(project._registry)}


def test_detect_coverage_flags_fact_table_without_metric(semantic_project_factory):
    from marivo.semantic.richness import _detect_coverage

    project = semantic_project_factory(_model(_FACT_NO_METRIC))
    gaps = _detect_coverage(project._registry)
    assert ("fact_table_no_metric", ("sales.orders",)) in gaps


def test_detect_coverage_clears_when_metric_present(semantic_project_factory):
    project = semantic_project_factory(_model(_FACT_WITH_METRIC))
    assert "fact_table_no_metric" not in _coverage_subkinds(project)


def test_detect_coverage_flags_shared_keys_without_relationship(semantic_project_factory):
    from marivo.semantic.richness import _detect_coverage

    project = semantic_project_factory(_model(_SHARED_KEYS_NO_REL))
    gaps = _detect_coverage(project._registry)
    assert (
        "dataset_shares_keys_no_relationship",
        ("sales.customers", "sales.orders"),
    ) in gaps


def test_detect_coverage_clears_shared_keys_with_relationship(semantic_project_factory):
    project = semantic_project_factory(_model(_SHARED_KEYS_WITH_REL))
    assert "dataset_shares_keys_no_relationship" not in _coverage_subkinds(project)


def test_gap_terms_include_ref_name_synonyms_examples_and_dataset_fields():
    from types import SimpleNamespace

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.richness import _gap_terms

    objects = {
        "sales.orders": SimpleNamespace(
            name="orders",
            ai_context=AiContextIR(
                synonyms=("bookings",),
                examples=("how many orders?",),
            ),
        )
    }
    fields_by_dataset = {
        "sales.orders": [
            SimpleNamespace(
                name="amount",
                ai_context=AiContextIR(
                    synonyms=("revenue",),
                    examples=("total amount by region",),
                ),
            )
        ]
    }
    assert _gap_terms(("sales.orders",), objects, fields_by_dataset) == {
        "orders",
        "bookings",
        "how many orders?",
        "amount",
        "revenue",
        "total amount by region",
    }


def test_demand_weight_none_is_zero():
    from marivo.semantic.richness import _demand_weight

    weight, evidence = _demand_weight(("sales.orders",), {"orders"}, None)
    assert weight == 0.0
    assert evidence == ()


def test_demand_weight_combines_signals():
    from marivo.semantic.richness import DemandSignal, _demand_weight

    demand = DemandSignal(
        example_questions=("what is total amount?",),
        intents=("amount trend",),
        run_history_refs=("sales.orders",),
        build_purpose="amount dashboard",
    )
    weight, evidence = _demand_weight(("sales.orders",), {"amount", "orders"}, demand)
    # history 3.0 + example 1.0 + intent 1.0 + build_purpose 0.5
    assert weight == 5.5
    assert "run_history:sales.orders" in evidence
    assert any(e.startswith("example:") for e in evidence)
    assert any(e.startswith("build_purpose:") for e in evidence)


def test_demand_weight_no_match_is_zero():
    from marivo.semantic.richness import DemandSignal, _demand_weight

    demand = DemandSignal(example_questions=("customer churn",))
    weight, evidence = _demand_weight(("sales.orders",), {"orders", "amount"}, demand)
    assert weight == 0.0
    assert evidence == ()


def test_build_richness_report_no_demand_lists_all_at_zero(semantic_project_factory):
    from marivo.semantic.richness import build_richness_report

    project = semantic_project_factory(_model(_FACT_NO_METRIC))
    report = build_richness_report(project)

    pairs = {(gap.kind, gap.subkind) for gap in report.gaps}
    assert ("coverage", "fact_table_no_metric") in pairs
    assert ("depth", "missing_business_definition") in pairs
    assert all(gap.demand_weight == 0.0 for gap in report.gaps)


def test_build_richness_report_demand_keeps_relevant_coverage(semantic_project_factory):
    from marivo.semantic.richness import DemandSignal, build_richness_report

    project = semantic_project_factory(_model(_FACT_NO_METRIC))
    # "amount" is a field of orders, so the coverage gap on orders is in demand.
    report = build_richness_report(
        project, demand=DemandSignal(example_questions=("total amount please",))
    )

    coverage = [g for g in report.gaps if g.subkind == "fact_table_no_metric"]
    assert coverage and coverage[0].demand_weight > 0.0
    assert report.gaps[0].demand_weight == max(g.demand_weight for g in report.gaps)


def test_build_richness_report_filters_undemanded_coverage(semantic_project_factory):
    from marivo.semantic.richness import DemandSignal, build_richness_report

    project = semantic_project_factory(_model(_FACT_NO_METRIC))
    report = build_richness_report(
        project, demand=DemandSignal(intents=("customer churn analysis",))
    )

    # Coverage gap dropped (no demand points at orders); depth gaps still listed.
    assert all(g.subkind != "fact_table_no_metric" for g in report.gaps)
    assert any(g.kind == "depth" for g in report.gaps)


def test_build_richness_report_empty_when_not_loaded(semantic_project_factory):
    from marivo.semantic.richness import build_richness_report

    project = semantic_project_factory(_model(_FACT_NO_METRIC), load=False)
    report = build_richness_report(project)
    assert report.gaps == ()
    assert report.checked_at


def test_project_richness_returns_ranked_report(semantic_project_factory):
    from marivo.semantic.richness import DemandSignal, RichnessReport

    project = semantic_project_factory(_model(_FACT_NO_METRIC))
    report = project.richness(demand=DemandSignal(run_history_refs=("sales.orders",)))

    assert isinstance(report, RichnessReport)
    weights = [gap.demand_weight for gap in report.gaps]
    assert weights == sorted(weights, reverse=True)
    # run-history demand on the orders dataset floats its coverage gap to the top.
    assert report.gaps[0].refs == ("sales.orders",)


def test_project_richness_default_demand_is_none(semantic_project_factory):
    project = semantic_project_factory(_model(_FACT_NO_METRIC))
    report = project.richness()
    assert any(gap.kind == "coverage" for gap in report.gaps)
    assert all(gap.demand_weight == 0.0 for gap in report.gaps)


def test_detect_depth_flags_missing_unit(semantic_project_factory):
    from marivo.semantic.richness import _detect_depth

    files = {
        "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
        "sales/objects.py": (
            "import marivo.semantic as ms\n"
            "orders = ms.entity(name='orders', datasource='warehouse', "
            "source=ms.table('orders'))\n"
            "@ms.metric(entities=[orders], decomposition=ms.sum(), name='bare_metric', "
            "additivity='additive', verification_mode='python_native')\n"
            "def bare_metric(orders):\n"
            "    return orders.amount.sum()\n"
            "@ms.metric(entities=[orders], decomposition=ms.sum(), name='priced_metric', "
            "additivity='additive', verification_mode='python_native', unit='CNY')\n"
            "def priced_metric(orders):\n"
            "    return orders.amount.sum()\n"
        ),
    }
    project = semantic_project_factory(files)
    gaps = {(kind, refs[0]) for kind, refs in _detect_depth(project._registry)}
    assert ("missing_unit", "sales.bare_metric") in gaps
    assert ("missing_unit", "sales.priced_metric") not in gaps


def test_richness_types_are_public():
    import marivo.semantic as ms

    for name in ("DemandSignal", "RichnessGap", "RichnessReport"):
        assert name not in ms.__all__
