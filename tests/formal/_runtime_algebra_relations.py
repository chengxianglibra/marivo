"""Governed SQL witnesses for joint membership, stage checks and civil bounds."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis as mv
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import CASES, _datasource_model
from tests.datasource.source_cases import SourceData, source_case
from tests.formal.reference.equivalence import assert_equivalent, assert_policies_agree, run_frame
from tests.formal.reference.evaluator import observe, select, top
from tests.formal.reference.model import Bag, Branch, Key, Occurrence, Read, Reducer, Result, Scope
from tests.formal.reference.relations import Relation, in_population, window_slices
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace, capture_source


@contextmanager
def _session(
    provider: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    tables: Mapping[str, SourceData],
    declarations: str,
    *,
    report_zone: str = "UTC",
) -> Iterator[mv.Session]:
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        cases = {}
        for name, data in tables.items():
            case = stack.enter_context(source_case(provider, profile, tmp_path, monkeypatch, data))
            # The next DuckDB case creates its table in the same file. Release
            # that fixture's read-only connection before opening another writer.
            if provider == "duckdb":
                case.session.close()
            cases[name] = case
        sources = []
        for name, case in cases.items():
            source = case.source
            assert isinstance(source, TableSourceIR)
            sources.append(
                f"{name}_source = md.table({source.table!r}, database={source.database!r})"
            )
        datasource_file = _datasource_model(provider, next(iter(cases.values())), monkeypatch)
        factory(
            {
                "datasources/warehouse.py": datasource_file,
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)\n",
                "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + "warehouse = ms.ref.datasource('warehouse')\n"
                + "\n".join(sources)
                + "\n"
                + declarations,
            }
        )
        yield mv.session.get_or_create("formal-runtime-relations", timezone=report_zone)


def _sum(groups: Mapping[Key, Bag], *, strict: bool = False) -> Branch:
    return Branch(Reducer("sum", strict=strict), groups)


def _check(expected: Result, query: mv.AnalysisFrame, trace: SourceTrace, label: str) -> None:
    submitted = len(trace.native_sql)
    optimized = run_frame(query, OptimizationPolicy.default())
    optimized_submissions = len(trace.native_sql) - submitted
    control = run_frame(query, OptimizationPolicy.disabled())
    control_submissions = len(trace.native_sql) - submitted - optimized_submissions
    assert_equivalent(expected, optimized)
    assert_equivalent(expected, control)
    assert_policies_agree(control, optimized)
    assert optimized_submissions > 0, label
    assert control_submissions > 0, label
    print(f"{label}: {optimized_submissions} default / {control_submissions} control submissions")


_FANOUT = """\
orders = ms.entity(name='orders', datasource=warehouse, source=orders_source, primary_key=['id'])
links = ms.entity(name='links', datasource=warehouse, source=links_source, primary_key=['id'])
tags = ms.entity(name='tags', datasource=warehouse, source=tags_source, primary_key=['id'])
order_id = ms.dimension_column(name='id', entity=orders, column='id')
link_order = ms.dimension_column(name='order_id', entity=links, column='order_id')
link_tag = ms.dimension_column(name='tag_id', entity=links, column='tag_id')
tag_id = ms.dimension_column(name='id', entity=tags, column='id')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
order_links = ms.relationship(name='order_links', from_entity=orders, to_entity=links, keys=[ms.join_on(order_id, link_order)])
link_tags = ms.relationship(name='link_tags', from_entity=links, to_entity=tags, keys=[ms.join_on(link_tag, tag_id)])
"""


@pytest.mark.runtime
@pytest.mark.parametrize("provider,profile", CASES, ids=[provider for provider, _ in CASES])
def test_live_shared_fanout_product(
    provider: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    facts = ((1, 10), (2, 30), (3, 100))
    links = ((1, 1, 2), (2, 1, 1), (3, 2, 1), (4, 2, 1))
    tables = {
        "orders": SourceData(
            "id BIGINT NOT NULL, amount BIGINT NOT NULL",
            ",".join(map(str, facts)),
            "id Int64, amount Int64",
            [{"id": key, "amount": value} for key, value in facts],
        ),
        "links": SourceData(
            "id BIGINT NOT NULL, order_id BIGINT NOT NULL, tag_id BIGINT NOT NULL",
            ",".join(map(str, links)),
            "id Int64, order_id Int64, tag_id Int64",
            [{"id": key, "order_id": order, "tag_id": tag} for key, order, tag in links],
        ),
        "tags": SourceData("id BIGINT NOT NULL", "(1),(2)", "id Int64", [{"id": 1}, {"id": 2}]),
    }
    bag = tuple(Occurrence((key,), value) for key, value in facts)
    joint: Relation = {
        (key,): tuple((link, tag) for link, order, tag in links if order == key) for key, _ in facts
    }
    allowed_links = (1, 3, 4)
    target = tuple((link, 1) for link in allowed_links)
    link_image: Relation = {
        key: tuple((link,) for link, _ in matches) for key, matches in joint.items()
    }
    tag_image: Relation = {
        key: tuple((tag,) for _, tag in matches) for key, matches in joint.items()
    }
    shared = in_population(bag, target, (joint,))
    independent = in_population(bag, target, (link_image, tag_image))
    assert tuple(row.identity for row in shared) == ((2,),)
    assert tuple(row.identity for row in independent) == ((1,), (2,))
    expected = observe(
        {"shared": _sum({(): shared}), "independent": _sum({(): independent})},
        {"shared": Read("shared"), "independent": Read("independent")},
        Scope("scalar"),
    )
    with _session(
        provider, profile, tmp_path, monkeypatch, semantic_project_factory, tables, _FANOUT
    ) as session:
        catalog = session.catalog
        amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
        order_links = catalog_value(catalog, "sales.order_links", kind="relationship")
        link_tags = catalog_value(catalog, "sales.link_tags", kind="relationship")
        link = mv.identity(mv.path(order_links))
        tag = mv.identity(mv.path(order_links, link_tags))
        selected_links = mv.coordinates(by=(link,), keys=tuple(((key,),) for key in allowed_links))
        selected_tags = mv.coordinates(by=(tag,), keys=(((1,),),))
        total = amount.sum(time=None)
        query = session.observe(
            {
                "shared": total.where(mv.in_population(mv.product(selected_links, selected_tags))),
                "independent": total.where(
                    mv.in_population(selected_links) & mv.in_population(selected_tags)
                ),
            }
        )
        _check(expected, query, capture_source(monkeypatch), f"{provider} shared witness")


@pytest.mark.runtime
@pytest.mark.parametrize("provider,profile", CASES, ids=[provider for provider, _ in CASES])
def test_live_obligatory_composite_witness(
    provider: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    facts = ((1, 2, 6, 11, 3), (2, 2, 7, 4, None), (3, 9, 7, 20, 1))
    columns = ("id", "tenant", "category", "amount", "extra")
    records: list[dict[str, object]] = [dict(zip(columns, row, strict=True)) for row in facts]
    data = SourceData(
        "id BIGINT, tenant BIGINT, category BIGINT, amount BIGINT, extra BIGINT",
        "(1,2,6,11,3),(2,2,7,4,NULL),(3,9,7,20,1)",
        "id Int64, tenant Int64, category Int64, amount Int64, extra Nullable(Int64)",
        records,
    )
    declarations = """\
facts = ms.entity(name='facts', datasource=warehouse, source=facts_source, primary_key=['id'])
tenant = ms.dimension_column(name='tenant', entity=facts, column='tenant')
category = ms.dimension_column(name='category', entity=facts, column='category')
amount = ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())
extra = ms.measure_column(name='extra', entity=facts, column='extra', additivity=ms.additive_all())
"""
    safe = _sum(
        {
            (tenant, category): (Occurrence((key,), value),)
            for key, tenant, category, value, _ in facts
        }
    )
    checked = _sum(
        {
            (tenant, category): (Occurrence((key,), extra),)
            for key, tenant, category, _, extra in facts
        },
        strict=True,
    )
    observed = observe(
        {"safe": safe, "checked": checked},
        {"safe": Read("safe"), "checked": Read("checked")},
        Scope("grouped"),
    )
    expected = top(select(observed, {"safe": Read("safe")}), 0, "safe")
    assert expected.keys == ()
    assert expected.witness("strict_null") == (2, 7)
    with _session(
        provider,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        {"facts": data},
        declarations,
    ) as session:
        catalog = session.catalog
        amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
        extra = catalog_value(catalog, "sales.facts.extra", kind="measure")
        tenant = catalog_value(catalog, "sales.facts.tenant", kind="dimension")
        category = catalog_value(catalog, "sales.facts.category", kind="dimension")
        query = session.observe(
            {"safe": amount.sum(time=None), "checked": extra.sum(time=None, nulls="error")},
            by=(tenant, category),
        )
        dropped = query.select({"safe": query["safe"]})
        _check(
            expected,
            dropped.top(0, by=dropped["safe"]),
            capture_source(monkeypatch),
            f"{provider} composite strict witness at top zero",
        )


@pytest.mark.runtime
@pytest.mark.parametrize("provider,profile", CASES, ids=[provider for provider, _ in CASES])
def test_live_civil_dst_boundary(
    provider: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    report_zone = "America/New_York"
    start = datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 5, tzinfo=timezone.utc)
    stamps = (
        start - timedelta(seconds=1),
        start,
        datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
        end - timedelta(seconds=1),
        end,
    )
    assert end - start == timedelta(hours=25)
    assert stamps[2].astimezone(ZoneInfo(report_zone)).fold == 0
    assert stamps[3].astimezone(ZoneInfo(report_zone)).fold == 1
    facts = tuple((key, stamp, 2**key) for key, stamp in enumerate(stamps))
    inserts = [
        f"({key},TIMESTAMP '{stamp.replace(tzinfo=None).isoformat(sep=' ')}',{value})"
        for key, stamp, value in facts
    ]
    data = SourceData(
        "id BIGINT, point TIMESTAMP, amount BIGINT",
        ",".join(inserts),
        "id Int64, point DateTime('UTC'), amount Int64",
        [{"id": key, "point": stamp, "amount": value} for key, stamp, value in facts],
    )
    selected = window_slices(
        tuple(Occurrence((key,), value, time=stamp) for key, stamp, value in facts),
        {(): (start, end)},
    )[()]
    assert tuple(row.identity for row in selected) == ((1,), (2,), (3,), (4,))
    expected = observe(
        {"value": _sum({row.identity: (row,) for row in selected})},
        {"value": Read("value")},
        Scope("grouped"),
    )
    # These governed profiles expose timezone-bearing physical columns.
    parser = (
        "ms.timestamp()" if provider in {"mysql", "clickhouse"} else "ms.timestamp(timezone='UTC')"
    )
    declarations = f"""\
facts = ms.entity(name='facts', datasource=warehouse, source=facts_source, primary_key=['id'])
identity = ms.dimension_column(name='identity', entity=facts, column='id')
point = ms.time_dimension_column(name='point', entity=facts, column='point', parse={parser})
amount = ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())
"""
    with _session(
        provider,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        {"facts": data},
        declarations,
        report_zone=report_zone,
    ) as session:
        catalog = session.catalog
        amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
        point = catalog_value(catalog, "sales.facts.point", kind="time_dimension")
        identity = catalog_value(catalog, "sales.facts.identity", kind="dimension")
        query = session.observe(
            {"value": amount.sum(time=None)},
            where=(point >= "2026-11-01") & (point < "2026-11-02"),
            by=(identity,),
        )
        _check(expected, query, capture_source(monkeypatch), f"{provider} DST civil boundary")
