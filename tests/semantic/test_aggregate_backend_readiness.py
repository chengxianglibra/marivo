"""Known aggregate/backend incompatibilities fail before source access."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NoReturn

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.engines.base import EngineProfile
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import RefPayloadV1
from marivo.semantic.check import run_check
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.reader import SemanticProject
from marivo.semantic.runtime_metric import RuntimeMetricExpr

ProjectFactory = Callable[[dict[str, str]], SemanticProject]
Backend = Literal["trino", "duckdb", "sqlite", "clickhouse"]


def _catalog(factory: ProjectFactory, backend: Backend = "trino") -> ms.SemanticCatalog:
    declarations = {
        "trino": "md.trino(name='warehouse', host='trino.example', catalog='hive', user_env='TRINO_USER')",
        "duckdb": "md.duckdb(name='warehouse', path=':memory:')",
        "sqlite": "md.sqlite(name='warehouse', path=':memory:')",
        "clickhouse": "md.clickhouse(name='warehouse', host='clickhouse.example', database='sales')",
    }
    project = factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + declarations[backend]
            + "\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            "ms.domain(name='sales', owner='Data', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\n"
            "import marivo.semantic as ms\n"
            "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
            "source=md.table('orders'))\n"
            "region = ms.dimension_column(name='region', entity=orders, column='region')\n"
            "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
            "additivity=ms.additive_all(), unit='USD')\n"
            "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
            "exact_p95 = ms.aggregate(name='exact_p95', measure=amount, agg=('percentile', 0.95))\n"
            "approx_p95 = ms.aggregate(name='approx_p95', measure=amount, agg=('approx_percentile', 0.95))\n"
            "exact_distinct = ms.aggregate(name='exact_distinct', measure=amount, agg='count_distinct')\n"
            "dependent = ms.linear(name='dependent', add=[exact_p95, exact_p95])\n",
        }
    )
    assert project.is_ready(), project.errors()
    return ms.SemanticCatalog(project)


def _no_source_access(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("Static readiness must not access a datasource")


def test_trino_exact_p95_blocks_without_source_access(
    semantic_project_factory: ProjectFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(EngineProfile, "open", _no_source_access)
    monkeypatch.setattr(DatasourceConnectionService, "use_backend", _no_source_access)
    monkeypatch.setattr(DatasourceConnectionService, "session_backend", _no_source_access)
    monkeypatch.setattr(ms.SemanticCatalog, "preview", _no_source_access)
    monkeypatch.setattr(ms.SemanticCatalog, "source_health", _no_source_access)
    catalog = _catalog(semantic_project_factory)
    ref = ms.ref.metric("sales.exact_p95")
    entry = catalog.metrics.get(ref.path)

    report = catalog.readiness(refs=[entry])

    assert report.scope == "semantic_static"
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == ()
    assert report.warnings == ()
    (issue,) = report.blockers
    assert issue.kind == "aggregate_backend_unsupported"
    assert issue.severity == "blocker"
    assert issue.refs == (ref.path,)
    assert issue.details == {
        "agg": ["percentile", 0.95],
        "target_ref": RefPayloadV1.from_ref(ms.ref.measure("sales.orders.amount")).to_dict(),
        "datasource": "warehouse",
        "backend": "trino",
        "expected": "source-native agg=('percentile', 0.95) with its declared exactness",
        "received": "backend=trino",
    }
    assert issue.catalog_definition_fingerprint == catalog.definition_fingerprint
    assert issue.repair is not None
    assert issue.repair.kind == "reauthor"
    assert issue.repair.help_target.surface == "semantic"
    assert issue.repair.help_target.canonical_id == "aggregate"
    assert "agg=('approx_percentile', 0.95)" in issue.repair.action
    assert "Execution never substitutes" in issue.repair.action
    assert "aggregate_backend_unsupported" in report.render()
    assert "backend=trino" in report.render()
    assert report.to_dict()["blockers"] == [issue.to_dict()]
    assert capsys.readouterr().out == ""
    marivo.help("semantic.aggregate")
    assert "exact" in capsys.readouterr().out

    source = tmp_path / "models/semantic/sales/models.py"
    source.write_text(
        source.read_text().replace("agg=('percentile', 0.95)", "agg=('approx_percentile', 0.95)")
    )
    repaired = ms.load(workspace_dir=tmp_path).readiness(refs=[ref])
    assert repaired.status == "ready"
    assert repaired.analysis_ready_inputs == (ref,)
    assert repaired.blockers == ()


@pytest.mark.parametrize(
    ("backend", "metric", "blocked", "repair_text"),
    (
        ("trino", "approx_p95", False, ""),
        ("duckdb", "exact_p95", False, ""),
        ("sqlite", "exact_p95", True, "does not support that source operation either"),
        ("sqlite", "approx_p95", True, "qualified source-native implementation"),
        ("clickhouse", "exact_distinct", True, "agg='approx_count_distinct'"),
    ),
)
def test_independent_aggregate_capability_boundaries(
    semantic_project_factory: ProjectFactory,
    backend: Backend,
    metric: str,
    blocked: bool,
    repair_text: str,
) -> None:
    catalog = _catalog(semantic_project_factory, backend)
    ref = ms.ref.metric(f"sales.{metric}")
    report = catalog.readiness(refs=[ref])
    assert report.status == ("blocked" if blocked else "ready")
    assert report.analysis_ready_inputs == (() if blocked else (ref,))
    if blocked:
        (issue,) = report.blockers
        assert issue.kind == "aggregate_backend_unsupported"
        assert issue.repair is not None and repair_text in issue.repair.action
    else:
        assert report.blockers == ()


def test_dependency_blocker_preserves_scoped_ready_input_order(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    exact = ms.ref.metric("sales.exact_p95")
    dependent = ms.ref.metric("sales.dependent")
    revenue = ms.ref.metric("sales.revenue")
    approximate = ms.ref.metric("sales.approx_p95")
    report = catalog.readiness(refs=[approximate, dependent, exact, revenue])
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == (approximate, revenue)
    (issue,) = report.blockers
    assert issue.refs == (exact.path,)

    scoped = catalog.readiness(refs=[revenue])
    assert scoped.status == "ready"
    assert scoped.blockers == ()
    assert scoped.analysis_ready_inputs == (revenue,)

    with pytest.raises(SemanticRuntimeError, match="unique"):
        catalog.readiness(refs=[exact, exact])


@pytest.mark.parametrize("wrapper", ("direct", "slice", "ratio", "linear"))
def test_runtime_aggregate_blocker_is_shared_and_deduplicated(
    semantic_project_factory: ProjectFactory,
    wrapper: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = _catalog(semantic_project_factory)
    exact = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.orders.amount"), agg=("percentile", 0.95), label="Exact p95"
    )
    expression: RuntimeMetricExpr = exact
    if wrapper == "slice":
        expression = mv.runtime_metric.slice(
            exact, by={ms.ref.dimension("sales.orders.region"): "north"}, label="North p95"
        )
    elif wrapper == "ratio":
        expression = mv.runtime_metric.ratio(
            exact, ms.ref.metric("sales.revenue"), label="P95 ratio"
        )
    elif wrapper == "linear":
        expression = mv.runtime_metric.linear(add=(exact, exact), label="Repeated p95")
    approximate = mv.runtime_metric.aggregate(
        ms.ref.measure("sales.orders.amount"),
        agg=("approx_percentile", 0.95),
        label="Approximate p95",
    )
    revenue = ms.ref.metric("sales.revenue")
    report = catalog.readiness(refs=[revenue, expression, approximate])
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == (revenue, approximate)
    (issue,) = report.blockers
    assert issue.kind == "aggregate_backend_unsupported"
    assert issue.refs[0].startswith("runtime:")
    assert issue.details["agg"] == ["percentile", 0.95]
    assert issue.catalog_definition_fingerprint == catalog.definition_fingerprint
    assert issue.repair is not None
    assert issue.repair.help_target.surface == "analysis"
    assert issue.repair.help_target.canonical_id == "runtime_metric.aggregate"
    assert "agg=('approx_percentile', 0.95)" in issue.repair.action
    marivo.help("analysis.runtime_metric.aggregate")
    assert "readiness" in capsys.readouterr().out
    assert exact.agg == ("percentile", 0.95)


def test_runtime_catalog_dependency_uses_its_original_repair(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    exact = ms.ref.metric("sales.exact_p95")
    expression = mv.runtime_metric.linear(add=(exact, exact), label="Catalog p95")
    report = catalog.readiness(refs=[expression])
    assert report.status == "blocked"
    assert report.analysis_ready_inputs == ()
    (issue,) = report.blockers
    assert issue.refs == (exact.path,)
    assert issue.repair is not None
    assert issue.repair.help_target.surface == "semantic"


def test_cli_readiness_inherits_aggregate_blocker(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    _catalog(semantic_project_factory)
    payload = run_check(workspace_dir=tmp_path, readiness=True)
    assert payload["status"] == "blocked"
    assert payload["errors"] == []
    domains = payload["readiness_by_domain"]
    assert isinstance(domains, dict)
    assert domains["sales"]["status"] == "blocked"
    assert domains["sales"]["blockers"][0]["kind"] == "aggregate_backend_unsupported"


def test_readiness_does_not_require_optional_backend_clients(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    _catalog(semantic_project_factory)
    script = """\
import importlib.abc
import sys

class NoBackendClients(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'trino', 'psycopg2', 'psycopg', 'MySQLdb', 'clickhouse_connect'}:
            raise AssertionError('Optional backend client imported: ' + fullname)
        return None

sys.meta_path.insert(0, NoBackendClients())
import marivo.semantic as ms
from marivo.datasource.engines.base import EngineProfile

def no_source(*args, **kwargs):
    raise AssertionError('Source opened during static readiness')

EngineProfile.open = no_source
catalog = ms.load(workspace_dir=sys.argv[1])
exact = ms.ref.metric('sales.exact_p95')
approximate = ms.ref.metric('sales.approx_p95')
report = catalog.readiness(refs=[exact, approximate])
assert report.status == 'blocked'
assert report.analysis_ready_inputs == (approximate,)
assert len(report.blockers) == 1
import marivo.analysis as mv
runtime = mv.runtime_metric.aggregate(ms.ref.measure('sales.orders.amount'), agg=('percentile', 0.95), label='P95')
assert catalog.readiness(refs=[runtime]).status == 'blocked'
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
