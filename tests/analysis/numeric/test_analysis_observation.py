"""Occurrence-bounded native Metrics preserve independent roots and components."""

from __future__ import annotations

import os
import subprocess
import sys

import duckdb
import pandas as pd
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.semantic._deferred_semantics import consume_field
from marivo.semantic.errors import SemanticError, SemanticLoadFailed
from tests.analysis.statistics.pandas_workflows import receipt_paths
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.paths import PROJECT_ROOT


def _observe(
    case: DslCase, expression: Deferred, *, windowed: bool = True, overall: bool = False
) -> mv.AnalysisFrame:
    session = case.session
    return session.observe(
        {"value": expression},
        during=session.time_scope(start="2026-08-01", end="2026-09-01") if windowed else None,
        by=() if overall else session.members(ms.ref.entity("sales.customer")),
        members=session.members(ms.ref.entity("sales.customer")) if overall else None,
    )


def _source_binding(frame: mv.AnalysisFrame) -> SourceFrameBinding:
    backing = frame._backing
    assert isinstance(backing, ExpressionFrameBacking)
    assert isinstance(backing.root, SourceFrameBinding)
    return backing.root


def _by_member(frame: pd.DataFrame) -> pd.Series:
    return frame.set_index(frame.columns[0])[("value", "value")]


def _extend(case: DslCase, *, physical: str = "", definitions: str = "") -> None:
    model = case.root / "models/semantic/sales/models.py"
    text = model.read_text().replace(
        "finalized = ms.finalize()", physical + "\nfinalized = ms.finalize()", 1
    )
    model.write_text(text + "\n" + definitions)
    ms.load(workspace_dir=case.root)


def _assert_cold_value(case: DslCase, artifact: str, expected: float | int) -> None:
    """Read an explicitly persisted source aggregate in an independent process."""
    offline = case.database_path.with_suffix(".offline")
    models = case.root / "models"
    models_offline = case.root / "models.offline"
    case.database_path.rename(offline)
    models.rename(models_offline)
    script = """
import math
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError('terminal output loaded Semantic or connected a source')
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1], by='id')
before = run_ids(session)
fixed = session.persisted_frame(sys.argv[2])
assert isinstance(fixed, mv.AnalysisFrame)
actual = fixed.to_pandas()[('value','value')].iloc[0]
assert math.isclose(actual, float(sys.argv[3]), rel_tol=1e-14), actual
assert run_ids(session) == before
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, case.session.id, artifact, str(expected)],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        offline.rename(case.database_path)
        models_offline.rename(models)
    assert result.returncode == 0, result.stderr


def test_explicit_field_paths_preserve_hop_order_without_source_read(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    region = case.session.catalog.fields.get("sales.customer.region")
    line_order = case.session.catalog.relationships.get("sales.line_order")
    buyer = case.session.catalog.relationships.get("sales.order_buyer")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Path construction read business rows")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    read = consume_field(region.via((line_order, buyer))).field_read
    assert read is not None
    assert tuple(item.ref.path for item in read.path) == ("sales.line_order", "sales.order_buyer")
    assert case.session.runs().items == ()
    with pytest.raises(SemanticError):
        consume_field(region.via((buyer, line_order)))


def test_observe_ratio_over_two_contribution_roots(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3", revenue_unit="1")
    session = case.session
    line_revenue = session.catalog.metrics.get("sales.line_revenue")
    order_count = session.catalog.metrics.get("sales.order_count")
    query = _observe(case, line_revenue / order_count)
    binding = _source_binding(query).graph.signature.observation
    assert binding is not None
    assert {item.root.ref.path for item in binding.contributions} == {
        "sales.order",
        "sales.order_line",
    }
    rows = _by_member(query.compute().to_pandas())
    # A: (40+60)/2; B: (20+40)/2. Roots reduce before their values combine.
    assert rows.to_dict() == {"A": 50.0, "B": 30.0}


def test_sliced_weighted_mean_filters_pairs_before_reduction(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    channel = session.catalog.fields.get("sales.order.channel")
    expression = amount.weighted_mean(weight=amount, time=time).where(channel == "web")
    observed = _observe(case, expression)
    rows = _by_member(observed.compute().to_pandas())
    assert rows.loc[["A", "C"]].tolist() == [450, 400]
    assert rows.loc[["B", "D"]].isna().all()
    expected = (450**2 + 400**2) / (450 + 400)
    assert _observe(case, expression, overall=True).compute().to_pandas()[
        ("value", "value")
    ].tolist() == pytest.approx([expected])


def test_observe_two_branches_of_one_root_over_a_single_route(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    channel = session.catalog.fields.get("sales.order.channel")
    web = amount.sum(time=time, empty="null").where(channel == "web")
    count = session.catalog.metrics.get("sales.order_count")
    observed = _observe(case, web / count)
    binding = _source_binding(observed).graph.signature.observation
    assert binding is not None and len(binding.contributions) == 2
    assert {item.root.ref.path for item in binding.contributions} == {"sales.order"}
    rows = _by_member(observed.compute().to_pandas())
    assert rows.loc[["A", "C"]].tolist() == [450.0, 400.0]
    assert pd.isna(rows.loc["B"]) and pd.isna(rows.loc["D"])


def test_observe_without_a_window_keeps_every_admitted_contribution(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    revenue = case.session.catalog.metrics.get("sales.revenue")
    rows = _by_member(_observe(case, revenue, windowed=False).compute().to_pandas())
    assert rows.loc[["A", "B", "C"]].tolist() == [450 + 99 + 77, 150, 400]


def test_windowed_metric_requires_an_endpoint(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    revenue = case.session.catalog.metrics.get("sales.revenue").window(mv.all_history())
    with pytest.raises(AnalysisError):
        _observe(case, revenue, windowed=False)
    # The explicit scope supplies the all-history endpoint, with September excluded.
    rows = _by_member(_observe(case, revenue).compute().to_pandas())
    assert rows.loc[["A", "B", "C"]].tolist() == [527, 150, 400]


def test_opaque_python_body_cannot_invent_metric_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    query = _observe(case, case.session.catalog.metrics.get("sales.revenue"))
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\n@ms.metric()\ndef opaque_revenue():\n    return amount_value.sum(time=time_value).resolve(None)\n"
    )
    with pytest.raises(SemanticLoadFailed, match="invalid_component_body"):
        ms.load(workspace_dir=case.root)
    assert _by_member(query.compute().to_pandas()).loc["A"] == 450


def test_linear_executes_more_than_two_occurrences(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    count = case.session.catalog.metrics.get("sales.order_count")
    assert _by_member(_observe(case, count + count + count).compute().to_pandas()).tolist() == [
        3,
        3,
        3,
        0,
    ]


def test_linear_preserves_each_empty_branch_policy(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1", revenue_unit="1")
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    time = session.catalog.fields.get("sales.order.ordered_at")
    channel = session.catalog.fields.get("sales.order.channel")
    count = session.catalog.metrics.get("sales.order_count")
    for expression, counted, nullable in (
        (count, True, False),
        (amount.sum(time=time, empty="zero"), False, False),
        (amount.sum(time=time), False, True),
    ):
        rows = _by_member(
            _observe(case, expression.where(channel == "web") + count).compute().to_pandas()
        )
        assert rows.loc[["A", "C"]].tolist() == ([2, 2] if counted else [451, 401])
        if nullable:
            assert rows.loc[["B", "D"]].isna().all()
        else:
            assert rows.loc[["B", "D"]].tolist() == [1, 0]


def test_anonymous_aggregate_resolves_the_declared_default_axis(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
    )
    ms.load(workspace_dir=case.root)
    amount = case.session.catalog.fields.get("sales.order.amount")
    for windowed in (False, True):
        rows = _by_member(
            _observe(case, amount.sum(empty="null"), windowed=windowed).compute().to_pandas()
        )
        assert rows.loc[["A", "B", "C"]].tolist() == [450 if windowed else 626, 150, 400]
        assert pd.isna(rows.loc["D"])


def test_linear_executes_three_independently_reduced_roots(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3", revenue_unit="1")
    _extend(
        case,
        physical="""
shipments = ms.entity(name='shipment', datasource=warehouse, source=md.table('order'), primary_key=['order_id'])
shipment_customer = ms.dimension_column(name='customer_id', entity=shipments, column='customer_id')
shipment_time = ms.time_dimension_column(name='ordered_at', entity=shipments, column='ordered_at', granularity='second', parse=ms.timestamp(timezone='UTC'))
shipment_buyer = ms.relationship(name='shipment_buyer', from_entity=shipments, to_entity=customer, keys=[ms.join_on(shipment_customer, customer_id)])
""",
        definitions="""
shipment_value = finalized.resolve(shipments)
shipment_clock = finalized.resolve(shipment_time)
@ms.metric()
def shipment_count():
    return shipment_value.count(time=shipment_clock)
""",
    )
    session = case.session
    expression = (
        session.catalog.metrics.get("sales.line_revenue")
        + session.catalog.metrics.get("sales.order_count")
        + session.catalog.metrics.get("sales.shipment_count")
    )
    query = _observe(case, expression)
    binding = _source_binding(query).graph.signature.observation
    assert binding is not None
    assert {item.root.ref.path for item in binding.contributions} == {
        "sales.order_line",
        "sales.order",
        "sales.shipment",
    }
    assert _by_member(query.compute().to_pandas()).tolist() == [104, 64]


def test_ratio_accepts_a_negative_sum_denominator(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('UPDATE "order" SET amount = -amount')
    session = case.session
    time = session.catalog.fields.get("sales.order.ordered_at")
    amount = session.catalog.fields.get("sales.order.amount")
    expression = session.catalog.metrics.get("sales.order_count") / amount.sum(
        time=time, empty="null"
    )
    observed = _observe(case, expression)
    rows = _by_member(observed.compute().to_pandas())
    assert rows.loc[["A", "B", "C"]].tolist() == pytest.approx([-1 / 450, -1 / 150, -1 / 400])
    assert pd.isna(rows.loc["D"])
    assert _observe(case, expression, overall=True).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [-3 / 1000]


def test_weighted_mean_preserves_pairs_and_original_rollup(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('ALTER TABLE "order" ADD COLUMN weight BIGINT DEFAULT 1')
        db.execute(
            "UPDATE \"order\" SET weight=CASE WHEN customer_id='A' THEN 2 WHEN customer_id='B' THEN 0 WHEN customer_id='C' THEN 4 ELSE NULL END"
        )
        db.execute(
            "INSERT INTO \"order\" VALUES ('null_value','A','web','paid','2026-08-15',NULL,9), ('null_weight','A','web','paid','2026-08-15',50,NULL), ('pair','A','web','paid','2026-08-15',100,1)"
        )
    _extend(
        case,
        physical="weight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n",
    )
    session = case.session
    amount = session.catalog.fields.get("sales.order.amount")
    weight = session.catalog.fields.get("sales.order.weight")
    time = session.catalog.fields.get("sales.order.ordered_at")
    expression = amount.weighted_mean(weight=weight, time=time)
    observed = _observe(case, expression)
    saved = observed.persist()
    rows = _by_member(saved.to_pandas())
    assert rows.loc["A"] == pytest.approx(1000 / 3)
    assert pd.isna(rows.loc["B"]) and pd.isna(rows.loc["D"])
    assert rows.loc["C"] == 400
    rolled = _observe(case, expression, overall=True).persist()
    assert rolled.to_pandas()[("value", "value")].tolist() == pytest.approx([2600 / 7])
    _assert_cold_value(case, rolled.artifact.id, 2600 / 7)
    path = receipt_paths(case.root, saved)[0]
    missing = path.with_name(path.name + ".missing")
    path.rename(missing)
    try:
        with pytest.raises(AnalysisError):
            saved.to_pandas()
        with pytest.raises(AnalysisError):
            session.persisted_frame(saved.artifact.id).to_pandas()
    finally:
        missing.rename(path)


def test_observation_preserves_full_member_identity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("ALTER TABLE customer ADD COLUMN tenant VARCHAR DEFAULT 'x'")
        db.execute("ALTER TABLE \"order\" ADD COLUMN tenant VARCHAR DEFAULT 'x'")
        db.execute("INSERT INTO customer VALUES ('A','north','y')")
        db.execute(
            "INSERT INTO \"order\" VALUES ('other_tenant','A','web','paid','2026-08-15',700,'y')"
        )
    model = case.root / "models/semantic/sales/models.py"
    text = model.read_text().replace(
        "primary_key=['customer_id']", "primary_key=['customer_id', 'tenant']"
    )
    text = text.replace(
        "buyer = ms.relationship(",
        "customer_tenant = ms.dimension_column(name='tenant', entity=customer, column='tenant')\norder_tenant = ms.dimension_column(name='tenant', entity=orders, column='tenant')\nbuyer = ms.relationship(",
    )
    text = text.replace(
        "keys=[ms.join_on(order_customer_id, customer_id)]",
        "keys=[ms.join_on(order_customer_id, customer_id), ms.join_on(order_tenant, customer_tenant)]",
    )
    model.write_text(text)
    ms.load(workspace_dir=case.root)
    session = case.session
    expression = session.catalog.metrics.get("sales.revenue") / session.catalog.metrics.get(
        "sales.order_count"
    )
    observed = _observe(case, expression)
    rows = _by_member(observed.compute().to_pandas()).to_dict()
    assert rows[("A", "x")] == 450 and rows[("A", "y")] == 700
    assert _observe(case, expression, overall=True).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [425]


def test_nested_linear_keeps_each_original_component(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1", revenue_unit="1")
    session = case.session
    time = session.catalog.fields.get("sales.order.ordered_at")
    revenue = session.catalog.fields.get("sales.order.amount").sum(time=time, empty="null")
    count = session.catalog.metrics.get("sales.order_count")
    expression = revenue - (revenue - count)
    observed = _observe(case, expression)
    rows = _by_member(observed.compute().to_pandas())
    assert rows.loc[["A", "B", "C"]].tolist() == [1, 1, 1]
    assert pd.isna(rows.loc["D"])
    assert _observe(case, expression, overall=True).compute().to_pandas()[
        ("value", "value")
    ].tolist() == [3]


def test_observation_rejects_wrong_or_extra_root_routes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    buyer = session.catalog.relationships.get("sales.order_buyer")
    line_order = session.catalog.relationships.get("sales.line_order")
    metric = session.catalog.metrics.get("sales.order_count")
    for path in ((line_order, buyer), (buyer, line_order), (buyer, buyer)):
        with pytest.raises((AnalysisError, SemanticError)):
            session.observe({"value": metric}, by=(region.via(path),))
    assert session.runs().items == ()


def test_linear_rejects_incommensurable_units(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    _extend(
        case,
        physical="amount_usd = ms.measure_column(name='amount_usd', entity=orders, column='amount', additivity=ms.additive_all(), unit='USD')\n",
        definitions="usd_value = finalized.resolve(amount_usd)\n@ms.metric()\ndef revenue_usd():\n    return usd_value.sum(time=time_value)\n",
    )
    session = case.session
    expression = session.catalog.metrics.get("sales.revenue") + session.catalog.metrics.get(
        "sales.revenue_usd"
    )
    with pytest.raises((SemanticError, AnalysisError, ValueError), match="unit"):
        _observe(case, expression)


def test_common_condition_reaches_each_leaf_before_reduction(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    time = session.catalog.fields.get("sales.order.ordered_at")
    revenue = session.catalog.fields.get("sales.order.amount").sum(time=time, empty="null")
    channel = session.catalog.fields.get("sales.order.channel")
    composite = revenue / session.catalog.metrics.get("sales.order_count")
    observed = session.observe(
        {"value": composite},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=session.members(ms.ref.entity("sales.customer")),
        where=channel == "web",
    )
    binding = _source_binding(observed).graph.signature.observation
    assert binding is not None and len(binding.contributions) == 2
    assert all(item.common_condition is not None for item in binding.contributions)
    rows = _by_member(observed.compute().to_pandas())
    assert rows.loc[["A", "C"]].tolist() == [450, 400]
    assert rows.loc[["B", "D"]].isna().all()
    assert session.observe(
        {"value": composite},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        members=session.members(ms.ref.entity("sales.customer")),
        where=channel == "web",
    ).compute().to_pandas()[("value", "value")].tolist() == [425]


@pytest.mark.parametrize(
    ("choices", "expected"),
    [(("web",), 850), (("web", None), 850), ((None,), 0), ((), 0), (("absent",), 0)],
)
def test_literal_membership_predicate_executes_in_typed_flow(
    analysis_dsl_case_factory: DslCaseFactory,
    choices: tuple[str | None, ...],
    expected: int,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    channel = session.catalog.fields.get("sales.order.channel")
    revenue = session.catalog.metrics.get("sales.revenue")
    period = session.time_scope(start="2026-08-01", end="2026-09-01")
    query = session.observe({"amount": revenue}, during=period, where=channel.isin(choices))
    saved = query.compute()
    assert saved.to_pandas()[("value", "amount")].tolist() == [expected]
