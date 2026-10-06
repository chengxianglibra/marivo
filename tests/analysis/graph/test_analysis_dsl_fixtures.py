"""Independent source and arithmetic oracles for the inactive DSL journeys.

These tests do not execute a DSL operator or assert that a future adapter is admitted.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import duckdb
import pytest

import marivo.semantic as ms
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    EntityDetails,
    RelationshipDetails,
    SimpleMetricDetails,
)
from tests.shared_fixtures import DslCase, DslCaseFactory, DslNames, DslScenario


def _sql(case: DslCase, query: str) -> list[tuple[object, ...]]:
    """Query fixture source facts without entering Marivo's analysis path."""
    connection = duckdb.connect(str(case.database_path), read_only=True)
    try:
        connection.execute("SET TimeZone = 'UTC'")
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def _metric_id(case: DslCase, name: str) -> str:
    return f"{case.names.domain}.{name}"


def test_real_declarations_load_with_distinct_roots_and_pending_authority(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    n = case.names
    for entity, key in (
        (n.customer, n.customer_id),
        (n.order, n.order_id),
        (n.order_line, n.line_id),
    ):
        entry = case.catalog.require(ms.ref.entity(f"{n.domain}.{entity}"))
        details = entry.details()
        assert isinstance(details, EntityDetails)
        assert details.primary_key == (key,)
        assert details.versioning is None

    for path in (
        f"{n.domain}.{n.customer}.{n.region}",
        f"{n.domain}.{n.order}.{n.channel}",
        f"{n.domain}.{n.order}.{n.ordered_at}",
    ):
        kind = ms.ref.time_dimension if path.endswith(n.ordered_at) else ms.ref.dimension
        assert case.catalog.require(kind(path)).ref.path == path

    buyer = case.catalog.require(ms.ref.relationship(_metric_id(case, n.buyer))).details()
    line_order = case.catalog.require(ms.ref.relationship(_metric_id(case, n.line_order))).details()
    assert isinstance(buyer, RelationshipDetails)
    assert isinstance(line_order, RelationshipDetails)
    assert buyer.from_entity.path == f"{n.domain}.{n.order}"
    assert buyer.to_entity.path == f"{n.domain}.{n.customer}"
    assert buyer.from_keys == (f"{n.domain}.{n.order}.{n.customer_id}",)
    assert buyer.to_keys == (f"{n.domain}.{n.customer}.{n.customer_id}",)
    assert line_order.from_entity.path == f"{n.domain}.{n.order_line}"
    assert line_order.to_entity.path == f"{n.domain}.{n.order}"

    for name, root in (
        (n.revenue, n.order),
        (n.order_count, n.order),
        (n.line_revenue, n.order_line),
    ):
        details = case.catalog.require(ms.ref.metric(_metric_id(case, name))).details()
        assert isinstance(details, SimpleMetricDetails)
        assert details.root_entity is not None
        assert details.root_entity.path == f"{n.domain}.{root}"
    ratio = case.catalog.require(ms.ref.metric(_metric_id(case, n.aov))).details()
    assert isinstance(ratio, DerivedMetricDetails)
    assert ratio.composition == "ratio"
    assert {name for name, _ in ratio.components} == {"numerator", "denominator"}

    assert case.session.project_root == case.root
    assert case.session.report_tz_name == "UTC"


def test_j1_source_and_independent_business_sql(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    assert _sql(case, 'SELECT COUNT(*) FROM "customer"') == [(4,)]
    assert _sql(
        case,
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'order' AND column_name = 'amount'",
    ) == [("BIGINT",)]
    assert _sql(
        case,
        """
        SELECT SUM(amount) FROM "order"
        WHERE ordered_at >= TIMESTAMPTZ '2026-08-01T00:00:00+00:00'
          AND ordered_at < TIMESTAMPTZ '2026-09-01T00:00:00+00:00'
        """,
    ) == [(1000,)]
    assert _sql(
        case,
        """
        SELECT c.region, SUM(o.amount) AS revenue
        FROM customer c LEFT JOIN "order" o
          ON o.customer_id = c.customer_id
         AND o.ordered_at >= TIMESTAMPTZ '2026-08-01T00:00:00+00:00'
         AND o.ordered_at < TIMESTAMPTZ '2026-09-01T00:00:00+00:00'
        GROUP BY c.region ORDER BY c.region
        """,
    ) == [("east", 600), ("south", 400), ("west", None)]
    assert _sql(
        case,
        """
        SELECT o.channel, SUM(o.amount) FROM customer c
        JOIN "order" o ON o.customer_id = c.customer_id
        WHERE c.region = 'east'
          AND o.ordered_at >= TIMESTAMPTZ '2026-08-01T00:00:00+00:00'
          AND o.ordered_at < TIMESTAMPTZ '2026-09-01T00:00:00+00:00'
        GROUP BY o.channel ORDER BY o.channel
        """,
    ) == [("mobile", 150), ("web", 450)]
    # July and September rows prove the half-open August boundary matters.
    assert _sql(case, 'SELECT COUNT(*) FROM "order"') == [(5,)]


def test_j2_decliners_and_equal_customer_weighting(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    monthly = _sql(
        case,
        """
        SELECT c.customer_id,
          COALESCE(SUM(CASE WHEN o.ordered_at >= TIMESTAMPTZ '2026-07-01T00:00:00+00:00'
                             AND o.ordered_at < TIMESTAMPTZ '2026-08-01T00:00:00+00:00'
                            THEN o.amount ELSE 0 END), 0) AS july,
          COALESCE(SUM(CASE WHEN o.ordered_at >= TIMESTAMPTZ '2026-08-01T00:00:00+00:00'
                             AND o.ordered_at < TIMESTAMPTZ '2026-09-01T00:00:00+00:00'
                            THEN o.amount ELSE 0 END), 0) AS august,
          COALESCE(SUM(CASE WHEN o.ordered_at >= TIMESTAMPTZ '2026-09-01T00:00:00+00:00'
                             AND o.ordered_at < TIMESTAMPTZ '2026-10-01T00:00:00+00:00'
                            THEN o.amount ELSE 0 END), 0) AS september
        FROM customer c LEFT JOIN "order" o ON o.customer_id = c.customer_id
        GROUP BY c.customer_id ORDER BY c.customer_id
        """,
    )
    assert monthly == [
        ("A", 100, 60, 30),
        ("B", 100, 120, 200),
        ("C", 50, 0, 0),
        ("D", 0, 0, 0),
    ]
    assert _sql(
        case,
        'SELECT order_id FROM "order" WHERE amount = 0 ORDER BY order_id',
    ) == [("j2_ac",), ("j2_ad",), ("j2_jd",), ("j2_sc",)]
    decliners: set[str] = set()
    for customer, july, august, _ in monthly:
        assert isinstance(customer, str)
        assert isinstance(july, int) and isinstance(august, int)
        if august < july:
            decliners.add(customer)
    assert decliners == {"A", "C"}
    assert Fraction(30 + 0, 2) == 15


def test_j3_independent_component_aggregation_and_two_distinct_means(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    assert _sql(
        case,
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'order_line' AND column_name = 'line_amount'",
    ) == [("BIGINT",)]
    components = _sql(
        case,
        """
        WITH orders_by_coordinate AS (
          SELECT customer_id, channel, COUNT(*) AS order_count
          FROM "order" WHERE ordered_at >= TIMESTAMPTZ '2026-08-01T00:00:00+00:00'
                         AND ordered_at < TIMESTAMPTZ '2026-09-01T00:00:00+00:00'
          GROUP BY customer_id, channel
        ), lines_by_coordinate AS (
          SELECT o.customer_id, o.channel, SUM(l.line_amount) AS line_revenue
          FROM order_line l JOIN "order" o ON l.order_id = o.order_id
          WHERE o.ordered_at >= TIMESTAMPTZ '2026-08-01T00:00:00+00:00'
            AND o.ordered_at < TIMESTAMPTZ '2026-09-01T00:00:00+00:00'
          GROUP BY o.customer_id, o.channel
        )
        SELECT counts.customer_id, counts.channel,
               COALESCE(lines.line_revenue, 0), counts.order_count
        FROM orders_by_coordinate counts LEFT JOIN lines_by_coordinate lines
          ON counts.customer_id = lines.customer_id AND counts.channel = lines.channel
        ORDER BY counts.customer_id, counts.channel
        """,
    )
    assert components == [
        ("A", "mobile", 0, 1),
        ("A", "web", 100, 1),
        ("B", "web", 60, 2),
    ]
    assert Fraction(100 + 60, 1 + 2) == Fraction(160, 3)
    assert Fraction(0, 1) == 0
    assert Fraction(100 + 0 + 60, 1 + 1 + 2) == 40
    assert (Fraction(100, 1) + Fraction(0, 1) + Fraction(60, 2)) / 3 == Fraction(130, 3)

    weighting = analysis_dsl_case_factory("j3_weighting")
    assert _sql(
        weighting,
        """
        WITH line_totals AS (
          SELECT o.customer_id, SUM(l.line_amount) AS amount
          FROM "order" o JOIN order_line l ON l.order_id = o.order_id
          GROUP BY o.customer_id
        ), order_counts AS (
          SELECT customer_id, COUNT(*) AS count FROM "order" GROUP BY customer_id
        )
        SELECT c.customer_id, l.amount, n.count FROM customer c
        JOIN line_totals l ON c.customer_id = l.customer_id
        JOIN order_counts n ON c.customer_id = n.customer_id
        ORDER BY c.customer_id
        """,
    ) == [("A", 100, 100), ("B", 100, 1)]
    assert (Fraction(100, 100) + Fraction(100, 1)) / 2 == Fraction(101, 2)
    assert Fraction(200, 101) != Fraction(101, 2)


def test_j4_pairing_and_independent_rank_arithmetic(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    assert _sql(
        case,
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'order' AND column_name = 'amount'",
    ) == [("DOUBLE",)]
    assert _sql(
        case,
        'SELECT customer_id, SUM(amount), COUNT(*) FROM "order" '
        "GROUP BY customer_id ORDER BY customer_id",
    ) == [("A", 1.0, 4), ("B", 2.0, 1), ("C", 4.0, 3), ("D", 8.0, 2)]
    revenue_ranks = (Fraction(1), Fraction(2), Fraction(3), Fraction(4))
    count_ranks = (Fraction(4), Fraction(1), Fraction(3), Fraction(2))
    center = Fraction(5, 2)
    cross = sum(
        (x - center) * (y - center) for x, y in zip(revenue_ranks, count_ranks, strict=True)
    )
    x_square = sum((x - center) ** 2 for x in revenue_ranks)
    y_square = sum((y - center) ** 2 for y in count_ranks)
    assert (cross, x_square, y_square) == (Fraction(-2), Fraction(5), Fraction(5))
    assert cross / 5 == Fraction(-2, 5)

    ties = analysis_dsl_case_factory("j4_ties")
    assert _sql(
        ties,
        'SELECT customer_id, SUM(amount), COUNT(*) FROM "order" '
        "GROUP BY customer_id ORDER BY customer_id",
    ) == [("A", 1.0, 1), ("B", 1.0, 1), ("C", 2.0, 3), ("D", 3.0, 2)]
    x_ties = (Fraction(3, 2), Fraction(3, 2), Fraction(3), Fraction(4))
    y_ties = (Fraction(3, 2), Fraction(3, 2), Fraction(4), Fraction(3))
    tied_cross = sum((x - center) * (y - center) for x, y in zip(x_ties, y_ties, strict=True))
    tied_square = sum((x - center) ** 2 for x in x_ties)
    assert (tied_cross, tied_square) == (Fraction(7, 2), Fraction(9, 2))
    assert tied_cross / tied_square == Fraction(7, 9)


@pytest.mark.parametrize(
    ("scenario", "query", "expected"),
    [
        pytest.param(
            "empty_domain", "SELECT COUNT(*) FROM customer", [(0,)], id="valid-empty-domain"
        ),
        pytest.param(
            "empty_group",
            "SELECT c.region, COALESCE(SUM(o.amount), 0) FROM customer c "
            'LEFT JOIN "order" o ON c.customer_id = o.customer_id '
            "GROUP BY c.region ORDER BY c.region",
            [("east", 10), ("west", 0)],
            id="valid-empty-group",
        ),
        pytest.param(
            "zero_denominator",
            'SELECT (SELECT COUNT(*) FROM "order"), (SELECT COUNT(*) FROM order_line)',
            [(0, 0)],
            id="ratio-finish-undefined-zero-denominator",
        ),
        pytest.param(
            "null_classification",
            "SELECT (SELECT COUNT(*) FROM customer WHERE region IS NULL), "
            '(SELECT COUNT(*) FROM "order" WHERE channel IS NULL)',
            [(1, 1)],
            id="source-classification-check",
        ),
        pytest.param(
            "missing_key",
            "SELECT (SELECT COUNT(*) FROM customer WHERE customer_id IS NULL), "
            '(SELECT COUNT(*) FROM "order" WHERE customer_id IS NULL), '
            "(SELECT COUNT(*) FROM order_line WHERE order_id IS NULL)",
            [(1, 1, 1)],
            id="source-key-check",
        ),
        pytest.param(
            "nonfinite",
            "SELECT SUM(CASE WHEN isnan(amount) THEN 1 ELSE 0 END), "
            'SUM(CASE WHEN isinf(amount) THEN 1 ELSE 0 END) FROM "order"',
            [(1, 1)],
            id="numeric-execution-reject-nonfinite",
        ),
        pytest.param(
            "overflow",
            'SELECT SUM(amount) FROM "order"',
            [(2**63,)],
            id="numeric-execution-reject-int64-overflow",
        ),
        pytest.param(
            "tuple_union",
            'SELECT channel, status FROM "order" GROUP BY channel, status ORDER BY channel, status',
            [("mobile", "cancelled"), ("web", "paid")],
            id="valid-complete-coordinate-tuples",
        ),
    ],
)
def test_edge_sources_preserve_future_admission_boundary(
    analysis_dsl_case_factory: DslCaseFactory,
    scenario: DslScenario,
    query: str,
    expected: list[tuple[object, ...]],
) -> None:
    case = analysis_dsl_case_factory(scenario)
    assert _sql(case, query) == expected
    if scenario == "tuple_union":
        assert _sql(
            case,
            "SELECT DISTINCT o.channel, o.status FROM order_line l "
            'JOIN "order" o ON l.order_id = o.order_id',
        ) == [("web", "paid")]


def test_cross_session_and_wrong_unit_are_separate_static_inputs(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    left = analysis_dsl_case_factory("j1")
    right = analysis_dsl_case_factory("j1", revenue_unit="USD")
    assert left.root != right.root
    assert left.database_path != right.database_path
    assert left.session.id != right.session.id
    assert left.session.project_root == left.root
    assert right.session.project_root == right.root
    left_revenue = left.catalog.require(
        ms.ref.metric(_metric_id(left, left.names.revenue))
    ).details()
    right_revenue = right.catalog.require(
        ms.ref.metric(_metric_id(right, right.names.revenue))
    ).details()
    assert isinstance(left_revenue, SimpleMetricDetails)
    assert isinstance(right_revenue, SimpleMetricDetails)
    assert (left_revenue.unit, right_revenue.unit) == ("CNY", "USD")


def test_renamed_domain_entities_and_fields_keep_the_same_loaded_contract(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    renamed = replace(
        DslNames(),
        domain="accounts",
        customer="account",
        order="purchase",
        order_line="purchase_item",
        customer_id="account_key",
        order_id="purchase_key",
        line_id="item_key",
        region="market",
        channel="touchpoint",
        status="state",
        ordered_at="purchased_at",
        amount="net_amount",
        line_amount="item_net_amount",
        buyer="purchase_account",
        line_order="item_purchase",
        revenue="net_sales",
        order_count="purchase_count",
        line_revenue="item_sales",
        aov="item_sales_per_purchase",
    )
    case = analysis_dsl_case_factory("j3", names=renamed)
    entity_details = case.catalog.require(ms.ref.entity("accounts.account")).details()
    metric_details = case.catalog.require(
        ms.ref.metric("accounts.item_sales_per_purchase")
    ).details()
    assert isinstance(entity_details, EntityDetails)
    assert isinstance(metric_details, DerivedMetricDetails)
    assert entity_details.primary_key == ("account_key",)
    assert metric_details.composition == "ratio"
    assert _sql(
        case,
        "SELECT p.touchpoint, SUM(i.item_net_amount) FROM purchase_item i "
        "JOIN purchase p ON i.purchase_key = p.purchase_key "
        "GROUP BY p.touchpoint",
    ) == [("web", 160)]
    assert _sql(case, "SELECT COUNT(*) FROM purchase") == [(4,)]
