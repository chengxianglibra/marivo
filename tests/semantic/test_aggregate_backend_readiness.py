"""Runtime aggregate certification checks exactness without source access."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NoReturn

import pytest

import marivo.semantic as ms
from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic import runtime_metric as runtime_descriptors
from marivo.semantic.check import run_check
from marivo.semantic.reader import SemanticProject
from marivo.semantic.runtime_metric import RuntimeMetricExpr

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
            "sales/models.py": """\
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
region = ms.dimension_column(name="region", entity=orders, column="region")
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
finalized = ms.finalize()
amount_value = finalized.resolve(amount)
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


@pytest.mark.parametrize("wrapper", ("direct", "slice", "ratio", "linear"))
def test_trino_exact_percentile_blocks_before_source_access(
    semantic_project_factory: ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
    wrapper: str,
) -> None:
    monkeypatch.setattr(EngineProfile, "open", _no_source_access)
    monkeypatch.setattr(DatasourceConnectionService, "use_backend", _no_source_access)
    monkeypatch.setattr(DatasourceConnectionService, "session_backend", _no_source_access)
    catalog = _catalog(semantic_project_factory)
    measure = ms.ref.measure("sales.orders.amount")
    exact = runtime_descriptors.aggregate(measure, agg=("percentile", 0.95), label="Exact p95")
    expression: RuntimeMetricExpr = exact
    if wrapper == "slice":
        expression = runtime_descriptors.slice(
            exact,
            by={ms.ref.dimension("sales.orders.region"): "north"},
            label="North p95",
        )
    elif wrapper == "ratio":
        total = runtime_descriptors.aggregate(measure, agg="sum", label="Total")
        expression = runtime_descriptors.ratio(exact, total, label="P95 ratio")
    elif wrapper == "linear":
        expression = runtime_descriptors.linear(add=(exact, exact), label="Repeated p95")
    approximate = runtime_descriptors.aggregate(
        measure, agg=("approx_percentile", 0.95), label="Approximate p95"
    )
    report = catalog.readiness(refs=[expression, approximate])
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == (approximate,)
    (issue,) = report.blockers
    assert issue.kind == "aggregate_backend_unsupported"
    assert issue.details["agg"] == ["percentile", 0.95]
    assert issue.details["backend"] == "trino"
    assert issue.catalog_definition_fingerprint == catalog.definition_fingerprint
    assert issue.repair is not None
    assert issue.repair.help_target.canonical_id == "runtime_metric.aggregate"
    assert "agg=('approx_percentile', 0.95)" in issue.repair.action
    assert exact.agg == ("percentile", 0.95)


@pytest.mark.parametrize(
    ("backend", "agg", "blocked"),
    (
        ("trino", ("approx_percentile", 0.95), False),
        ("duckdb", ("percentile", 0.95), False),
        ("clickhouse", "count_distinct", True),
    ),
)
def test_independent_backend_exactness_boundaries(
    semantic_project_factory: ProjectFactory,
    backend: Backend,
    agg: Literal["count_distinct"] | tuple[Literal["percentile", "approx_percentile"], float],
    blocked: bool,
) -> None:
    catalog = _catalog(semantic_project_factory, backend)
    expression = runtime_descriptors.aggregate(
        ms.ref.measure("sales.orders.amount"), agg=agg, label="Checked"
    )
    report = catalog.readiness(refs=[expression])
    assert report.status == ("blocked" if blocked else "ready")
    assert report.analysis_ready_inputs == (() if blocked else (expression,))
    if blocked:
        (issue,) = report.blockers
        assert issue.kind == "aggregate_backend_unsupported"
        assert issue.repair is not None and "approx_count_distinct" in issue.repair.action
    else:
        assert report.blockers == ()


def test_published_metrics_direct_certification_routes_to_physical_inputs(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    with pytest.raises(ms.errors.SemanticRuntimeError) as caught:
        catalog.readiness(refs=[catalog.metrics.get("sales.revenue")])
    assert caught.value.kind == "invalid_expression_stage"
    assert caught.value.repair is not None
    assert "physical fields and Entities" in caught.value.repair.action


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
        if fullname.split('.')[0] in {'trino','psycopg2','psycopg','MySQLdb','clickhouse_connect'}:
            raise AssertionError('Optional client imported: ' + fullname)
sys.meta_path.insert(0, NoClients())
import marivo.semantic as ms
from marivo.semantic import runtime_metric as runtime_descriptors
catalog = ms.load(workspace_dir=sys.argv[1])
amount = ms.ref.measure('sales.orders.amount')
exact = runtime_descriptors.aggregate(amount, agg=('percentile',0.95), label='Exact')
approximate = runtime_descriptors.aggregate(amount, agg=('approx_percentile',0.95), label='Approximate')
report = catalog.readiness(refs=[exact, approximate])
assert report.status == 'blocked'
assert report.analysis_ready_inputs == (approximate,)
assert len(report.blockers) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
