"""Physical readiness is independent of numerical execution qualification."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NoReturn

import pytest

import marivo.semantic as ms
from marivo.semantic.check import run_check
from marivo.semantic.ir import AggKind
from marivo.semantic.reader import SemanticProject

ProjectFactory = Callable[[dict[str, str]], SemanticProject]
Backend = Literal["trino", "duckdb", "clickhouse"]


def _catalog(factory: ProjectFactory, backend: Backend = "trino") -> ms.SemanticCatalog:
    declarations = {
        "trino": "md.trino(name='warehouse', host='trino.example', catalog='hive', user_env='TRINO_USER')",
        "duckdb": "md.duckdb(name='warehouse', path=':memory:')",
        "clickhouse": "md.clickhouse(name='warehouse', host='clickhouse.example', database='sales')",
    }
    project = factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + declarations[backend]
            + "\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/models.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
region = ms.dimension_column(name="region", entity=orders, column="region")
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
amount_value = amount
@ms.metric()
def revenue():
    return amount_value.sum()
""",
        }
    )
    assert project.is_ready(), project.errors()
    return ms.SemanticCatalog(project)


def _no_source_access(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("Static readiness must not access a datasource")


def test_published_metrics_direct_certification_routes_to_physical_inputs(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    report = catalog.readiness(refs=[catalog.get("metric:sales.revenue")])
    assert report.requested_ids == ("metric:sales.revenue",)
    assert "measure:sales.orders.amount" in report.checked_ids
    assert report._analysis_ready_inputs == ()


def test_cli_certifies_current_physical_inputs(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    _catalog(semantic_project_factory)
    payload = run_check(workspace_dir=tmp_path, readiness=True)
    assert payload["errors"] == []


def test_readiness_does_not_import_optional_backend_clients(
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
) -> None:
    _catalog(semantic_project_factory)
    script = """\
import importlib.abc
import sys
class NoClients(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'trino','psycopg2','psycopg','pymysql','clickhouse_connect'}:
            raise AssertionError('Optional client imported: ' + fullname)
sys.meta_path.insert(0, NoClients())
import marivo.semantic as ms
catalog = ms.load(workspace_dir=sys.argv[1])
amount = ms.ref.measure('sales.orders.amount')
report = catalog.readiness(refs=[amount])
assert report.status == 'ready'
assert report._analysis_ready_inputs == (__import__('marivo._symbolic', fromlist=['native_symbol_ref']).native_symbol_ref(amount),)
assert not report.blockers
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("backend", "aggregate", "unsupported"),
    (
        ("trino", ("percentile", 0.95), True),
        ("trino", ("approx_percentile", 0.95), False),
        ("duckdb", ("percentile", 0.95), False),
        ("clickhouse", "count_distinct", False),
    ),
)
def test_aggregate_accuracy_remains_owned_by_execution(
    backend: Backend,
    aggregate: AggKind,
    unsupported: bool,
) -> None:
    from marivo.semantic._aggregate_accuracy import aggregate_repair

    action = aggregate_repair(aggregate, backend)
    assert (action is not None) == unsupported
    if action is not None and isinstance(aggregate, tuple):
        assert str(aggregate[1]) in action
