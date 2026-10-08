"""Build standalone governed projects for public Analysis DSL journeys."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import duckdb

Journey = Literal["j1", "j2", "j3", "j4"]

_AUGUST = "2026-08-15T12:00:00+00:00"

CUSTOMERS: dict[Journey, tuple[tuple[str, str], ...]] = {
    "j1": (("A", "east"), ("B", "east"), ("C", "south"), ("D", "west")),
    "j2": (("A", "east"), ("B", "east"), ("C", "south"), ("D", "west")),
    "j3": (("A", "east"), ("B", "east")),
    "j4": tuple((customer, "east") for customer in "ABCD"),
}

ORDERS: dict[Journey, tuple[tuple[str, str, str, str, str, int | float], ...]] = {
    "j1": (
        ("j1_july", "A", "web", "paid", "2026-07-31T23:59:59+00:00", 77),
        ("j1_a", "A", "web", "paid", "2026-08-01T00:00:00+00:00", 450),
        ("j1_b", "B", "mobile", "paid", _AUGUST, 150),
        ("j1_c", "C", "web", "paid", "2026-08-31T23:59:59+00:00", 400),
        ("j1_september", "A", "mobile", "paid", "2026-09-01T00:00:00+00:00", 99),
    ),
    "j2": (
        ("j2_ja", "A", "web", "paid", "2026-07-10T12:00:00+00:00", 100),
        ("j2_jb", "B", "web", "paid", "2026-07-10T12:00:00+00:00", 100),
        ("j2_jc", "C", "web", "paid", "2026-07-10T12:00:00+00:00", 50),
        ("j2_jd", "D", "web", "paid", "2026-07-10T12:00:00+00:00", 0),
        ("j2_aa", "A", "web", "paid", "2026-08-01T00:00:00+00:00", 60),
        ("j2_ab", "B", "web", "paid", _AUGUST, 120),
        ("j2_ac", "C", "web", "paid", _AUGUST, 0),
        ("j2_ad", "D", "web", "paid", _AUGUST, 0),
        ("j2_sa", "A", "web", "paid", "2026-09-01T00:00:00+00:00", 30),
        ("j2_sb", "B", "web", "paid", "2026-09-10T12:00:00+00:00", 200),
        ("j2_sc", "C", "web", "paid", "2026-09-10T12:00:00+00:00", 0),
    ),
    "j3": (
        ("j3_aw", "A", "web", "paid", _AUGUST, 0),
        ("j3_am", "A", "mobile", "paid", _AUGUST, 0),
        ("j3_bw1", "B", "web", "paid", _AUGUST, 0),
        ("j3_bw2", "B", "web", "paid", _AUGUST, 0),
    ),
    "j4": tuple(
        (f"j4_{customer}_{index}", customer, "web", "paid", _AUGUST, total if index == 0 else 0.0)
        for customer, count, total in zip("ABCD", (4, 1, 3, 2), (1.0, 2.0, 4.0, 8.0), strict=True)
        for index in range(count)
    ),
}

LINES: dict[Journey, tuple[tuple[str, str, int], ...]] = {
    "j1": (),
    "j2": (),
    "j3": (
        ("j3_l1", "j3_aw", 40),
        ("j3_l2", "j3_aw", 60),
        ("j3_l3", "j3_bw1", 20),
        ("j3_l4", "j3_bw2", 40),
    ),
    "j4": (),
}


def _models() -> str:
    return """\
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource('warehouse')
customer = ms.entity(name='customer', datasource=warehouse,
                     source=md.table('customer'), primary_key=['customer_id'])
orders = ms.entity(name='order', datasource=warehouse,
                   source=md.table('order'), primary_key=['order_id'])
lines = ms.entity(name='order_line', datasource=warehouse,
                  source=md.table('order_line'), primary_key=['line_id'])

customer_id = ms.dimension_column(name='customer_id', entity=customer, column='customer_id')
order_customer_id = ms.dimension_column(name='customer_id', entity=orders, column='customer_id')
order_id = ms.dimension_column(name='order_id', entity=orders, column='order_id')
line_order_id = ms.dimension_column(name='order_id', entity=lines, column='order_id')
region = ms.dimension_column(name='region', entity=customer, column='region')
channel = ms.dimension_column(name='channel', entity=orders, column='channel')
status = ms.dimension_column(name='status', entity=orders, column='status')
ordered_at = ms.time_dimension_column(name='ordered_at', entity=orders,
                                      column='ordered_at', granularity='second',
                                      parse=ms.timestamp(timezone='UTC'))
amount = ms.measure_column(name='amount', entity=orders, column='amount',
                           additivity=ms.additive_all(), unit='CNY')
line_amount = ms.measure_column(name='line_amount', entity=lines, column='line_amount',
                                additivity=ms.additive_all(), unit='CNY')
buyer = ms.relationship(name='order_buyer', from_entity=orders, to_entity=customer,
                        keys=[ms.join_on(order_customer_id, customer_id)])
line_order = ms.relationship(name='line_order', from_entity=lines, to_entity=orders,
                             keys=[ms.join_on(line_order_id, order_id)])
revenue = ms.aggregate(name='revenue', measure=amount, agg='sum', time=ordered_at)
order_count = ms.count(name='order_count', entity=orders, time=ordered_at)
line_revenue = ms.aggregate(name='line_revenue', measure=line_amount, agg='sum',
                            time=ordered_at, time_via=(line_order,),
                            nulls=ms.nulls.ignore(), empty=ms.empty.zero())
aov = ms.ratio(name='aov_from_lines', numerator=line_revenue, denominator=order_count,
               zero_denominator=ms.zero_denominator.undefined())
"""


def prepare(root: Path, journey: Journey) -> dict[str, str]:
    """Create one self-contained project using public authoring declarations."""
    root.mkdir(parents=True, exist_ok=False)
    database = root / "warehouse.duckdb"
    numeric_type = "DOUBLE" if journey == "j4" else "BIGINT"
    connection = duckdb.connect(str(database))
    try:
        connection.execute("SET threads = 1")
        connection.execute("CREATE TABLE customer (customer_id VARCHAR, region VARCHAR)")
        connection.execute(
            'CREATE TABLE "order" (order_id VARCHAR, customer_id VARCHAR, '
            f"channel VARCHAR, status VARCHAR, ordered_at TIMESTAMPTZ, amount {numeric_type})"
        )
        connection.execute(
            "CREATE TABLE order_line (line_id VARCHAR, order_id VARCHAR, "
            f"line_amount {numeric_type})"
        )
        connection.executemany("INSERT INTO customer VALUES (?, ?)", CUSTOMERS[journey])
        connection.executemany('INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)', ORDERS[journey])
        if LINES[journey]:
            connection.executemany("INSERT INTO order_line VALUES (?, ?, ?)", LINES[journey])
    finally:
        connection.close()

    (root / "marivo.toml").write_text('[project]\nname = "analysis-dsl-s4-p3"\n')
    datasource = root / "models" / "datasources" / "warehouse.py"
    datasource.parent.mkdir(parents=True)
    datasource.write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    semantic = root / "models" / "semantic" / "sales"
    semantic.mkdir(parents=True)
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='P3 acceptance', default=True)\n"
    )
    (semantic / "models.py").write_text(_models())
    digest = hashlib.sha256()
    for path in (
        database,
        root / "marivo.toml",
        datasource,
        semantic / "_domain.py",
        semantic / "models.py",
    ):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    facts = json.dumps(
        (CUSTOMERS[journey], ORDERS[journey], LINES[journey]),
        separators=(",", ":"),
    ).encode()
    return {
        "project": str(root),
        "journey": journey,
        "fixture_sha256": digest.hexdigest(),
        "facts_sha256": hashlib.sha256(facts).hexdigest(),
    }
