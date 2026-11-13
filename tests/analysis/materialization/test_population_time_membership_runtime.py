"""Backend execution of joint half-open interval membership."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value, datasource_arguments_source


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_overlapping_interval_membership_matches_joint_keys_once(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    date_literal = "DATE " if backend == "trino" else ""
    data = SourceData(
        "id BIGINT NOT NULL, day DATE NOT NULL, customer BIGINT NOT NULL, amount BIGINT NOT NULL",
        f"(1,{date_literal}'2026-08-01',1,10),(2,{date_literal}'2026-08-02',1,20),"
        f"(3,{date_literal}'2026-08-02',2,100),(4,{date_literal}'2026-08-03',1,30),"
        f"(5,{date_literal}'2026-08-04',2,200)",
        "id Int64, day Date, customer Int64, amount Int64",
        [
            {"id": key, "day": date(2026, 8, day), "customer": customer, "amount": amount}
            for key, day, customer, amount in (
                (1, 1, 1, 10),
                (2, 2, 1, 20),
                (3, 2, 2, 100),
                (4, 3, 1, 30),
                (5, 4, 2, 200),
            )
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        table = f"md.table({case.source.table!r}, database={case.source.database!r})"
        datasource = case.session.datasource
        arguments = {
            **datasource.fields,
            **{key: md.env(value) for key, value in datasource.env_refs.items()},
        }
        if isinstance(arguments.get("user"), str):
            monkeypatch.setenv("MARIVO_TIME_MEMBER_READER", str(arguments.pop("user")))
            arguments["user"] = md.env("MARIVO_TIME_MEMBER_READER")
        argument_text = datasource_arguments_source(arguments)
        semantic_project_factory(
            {
                "datasources/warehouse.py": f"import marivo.datasource as md\nmd.{backend}(wall_clock_timezone='UTC', name='warehouse', {argument_text})\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)\n",
                "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source={table}, primary_key=['id'])
day = ms.time_dimension_column(name='day', entity=orders, column='day')
customer = ms.dimension_column(name='customer', entity=orders, column='customer')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
""",
            }
        )
        session = mv.session.get_or_create("time-membership", timezone="UTC")
        day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
        customer = catalog_value(session.catalog, "sales.orders.customer", kind="dimension")
        amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
        first = session.time_scope(start="2026-08-01", end="2026-08-03")
        second = session.time_scope(start="2026-08-02", end="2026-08-04")
        one = mv.coordinates(by=(customer,), keys=((1,),))
        two = mv.coordinates(by=(customer,), keys=((2,),))
        selected = (
            mv.product(first, one).union(mv.product(second, one)).union(mv.product(second, two))
        )
        assert isinstance(selected, mv.Population)
        # Order 2 matches both intervals and counts once. Order 5 falls on the open end.
        metric = amount.sum(time=day)
        matched = mv.in_population(selected)
        result = session.observe(
            {"selected": metric.where(matched), "other": metric.where(~matched)}
        )
        assert result.compute().to_pandas().to_dict("list") == {"selected": [160], "other": [200]}
