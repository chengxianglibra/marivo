"""Three-process decimal and int64 retained rollup equality journey.

The producer authors a session, aggregates a decimal pair and an int64 pair
under a runtime Linear each, persists the checkpoint, and removes the source.
The cold process recovers the checkpoint by name, rolls it up to the month
grain from retained state only, and returns the exact values with their
original dtypes. The renamed source file makes any primary query fail loudly,
so a successful cold run is itself the zero-source-query proof.
"""

import json
import os
import sys
from pathlib import Path

DAY_ROWS = (
    (1, "2026-07-01", "10.05", "2.50", 3),
    (2, "2026-07-01", "20.10", "1.25", 1),
    (3, "2026-07-02", "23.99", "0.10", 1),
)
METRIC_NAMES = ("gmv", "net_amount", "net_qty", "amount_mean")
# Hand-computed month-grain expectations over the three DAY_ROWS:
# gmv = 10.05 + 20.10 + 23.99 = 54.14; cost_total = 2.50 + 1.25 + 0.10 = 3.85;
# net_amount = gmv + cost_total - gmv = 3.85; net_qty = qty_total + qty_count
# - qty_total = 5 + 3 - 5 = 3; amount_mean = quantity mean = 5 / 3 (one float
# division). The mean runs over the int64 measure because a mean over the
# decimal measure declares a decimal logical type that every backend rejects.
MONTH_EXPECTED = {
    "gmv": "54.14",
    "net_amount": "3.85",
    "net_qty": "3",
    "amount_mean": repr(5 / 3),
}


def _author(project: Path, *, with_mean: bool = False, mean_measure: str = "quantity") -> None:
    (project / "marivo.toml").write_text('[project]\nname = "rollup-equation"\n')

    datasource = project / "models" / "datasources"
    semantic = project / "models" / "semantic" / "sales"
    datasource.mkdir(parents=True)
    semantic.mkdir(parents=True)
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='warehouse.duckdb')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
    )
    (semantic / "orders.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders', columns={'id': md.source_column('id', data_type='int64'), "
        "'order_day': md.source_column('day', data_type='date'), "
        "'amount': md.source_column('amount', data_type='decimal(12,2)'), "
        "'cost': md.source_column('cost', data_type='decimal(12,2)'), "
        "'quantity': md.source_column('quantity', data_type='int64')}), primary_key=['id'])\n"
        "day = ms.time_dimension_column(name='day', entity=orders, column='order_day', "
        "granularity='day')\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
        "additivity='additive')\n"
        "cost = ms.measure_column(name='cost', entity=orders, column='cost', "
        "additivity='additive')\n"
        "quantity = ms.measure_column(name='quantity', entity=orders, column='quantity', "
        "additivity='additive')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "cost_total = ms.aggregate(name='cost_total', measure=cost, agg='sum')\n"
        "qty_total = ms.aggregate(name='qty_total', measure=quantity, agg='sum')\n"
        "qty_count = ms.aggregate(name='qty_count', measure=quantity, agg='count')\n"
        + (
            "amount_mean = ms.aggregate(name='amount_mean', measure="
            + mean_measure
            + ", agg='mean')\n"
            if with_mean
            else ""
        )
    )


def author_decimal_mean_project(project: Path) -> Path:
    """Author the same project plus a decimal-rooted mean Metric; no source rows.

    Returns the project directory so a caller can load a Session against it and
    observe the admission-rejected mean Metric without executing the source.
    The declared warehouse file exists with the declared schema so compilation
    can open it read-only before the structured rejection fires.
    """
    import duckdb

    _author(project, with_mean=True, mean_measure="amount")
    with duckdb.connect(str(project / "warehouse.duckdb")) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), "
            "cost DECIMAL(12,2), quantity BIGINT)"
        )
    return project


def run(mode: str, project: Path, artifact: str = "") -> dict[str, object]:
    import duckdb

    import marivo.analysis as mv
    import marivo.semantic as ms

    # Session and authoring resolution anchor on the current working directory,
    # exactly as an agent's write-run-read loop does.
    os.chdir(project)
    if mode == "produce":
        _author(project, with_mean=True)
        with duckdb.connect(str(project / "warehouse.duckdb")) as connection:
            connection.execute(
                "CREATE TABLE orders(id BIGINT, day DATE, amount DECIMAL(12,2), "
                "cost DECIMAL(12,2), quantity BIGINT)"
            )
            connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", DAY_ROWS)
        from marivo.analysis import grain
        from marivo.analysis import runtime_metric as rm

        gmv = ms.ref.metric("sales.gmv")
        cost_total = ms.ref.metric("sales.cost_total")
        qty_total = ms.ref.metric("sales.qty_total")
        qty_count = ms.ref.metric("sales.qty_count")
        net_amount = rm.linear(add=[gmv, cost_total], subtract=[gmv], label="net_amount")
        net_qty = rm.linear(add=[qty_total, qty_count], subtract=[qty_total], label="net_qty")
        session = mv.session.get_or_create("equation", report_timezone="UTC")
        daily = (
            session.observe([gmv, net_amount, net_qty, ms.ref.metric("sales.amount_mean")])
            .with_time_axis(ms.ref.time_dimension("sales.orders.day"), grain=grain("day"))
            .aggregate()
        )
        warm_monthly = daily.rollup(grain=grain("month")).execute().to_pandas()
        checkpoint = daily.execute()
        (project / "warehouse.duckdb").rename(project / "warehouse.offline")
        return {
            "pid": os.getpid(),
            "session": session.id,
            "artifact": checkpoint.state.artifact_ref.ref,
            "warm_monthly": {name: str(warm_monthly[name].iloc[0]) for name in METRIC_NAMES},
        }
    session = mv.session.resume("equation")
    checkpoint = session.artifact(artifact)
    from marivo.analysis import grain

    cold = checkpoint.rollup(grain=grain("month")).execute()
    frame = cold.to_pandas()
    return {
        "pid": os.getpid(),
        "values": {name: str(frame[name].iloc[0]) for name in METRIC_NAMES},
        "dtypes": {name: str(frame[name].dtype) for name in METRIC_NAMES},
        "day_label": str(frame["day"].iloc[0]),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            run(sys.argv[1], Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else ""),
            sort_keys=True,
            allow_nan=False,
        )
    )
