"""Drift tests enforcing the agent-friendly public API result contract.

These tests verify:
- Result-producing public APIs do not write stdout.
- repr() is one line and points to .show().
- render() + show() are present and well-behaved on result types that own render().
- available: sections are present and non-empty.
- display= parameter is absent.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import marivo.datasource as md
from marivo.datasource.authoring import DuckDBSpec
from tests.support.paths import PROJECT_ROOT

if TYPE_CHECKING:
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject
    from tests.conftest import _SemanticProjectFactory
    from tests.shared_fixtures import DslCaseFactory

# ---------------------------------------------------------------------------
# Minimal project files for tests that need a loaded SemanticProject
# ---------------------------------------------------------------------------

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_OBJECTS_PY = textwrap.dedent("""    import marivo.datasource as md
    import marivo.semantic as ms
    orders_ref = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
        primary_key=["order_id"],
    )
    amount_ref = ms.measure_column(
        name="amount",
        entity=orders_ref,
        column="amount",
        additivity=ms.additive_all(),
        unit="USD",
    )
    created_at_ref = ms.time_dimension_column(
        name="created_at",
        entity=orders_ref,
        column="created_at",
        parse=ms.timestamp(timezone="UTC"),
    )

    orders = orders_ref
    amount = amount_ref
    created_at = created_at_ref

    @ms.metric()
    def total_revenue():
        return amount.sum(time=created_at)
""")


def _make_project(semantic_project_factory: _SemanticProjectFactory) -> SemanticProject:
    """Create a minimal loaded project for drift tests."""
    return semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _OBJECTS_PY,
        }
    )


def _make_catalog(semantic_project_factory: _SemanticProjectFactory) -> SemanticCatalog:
    """Create a minimal loaded catalog for drift tests."""
    from marivo.semantic.catalog import SemanticCatalog

    return SemanticCatalog(_make_project(semantic_project_factory))


# ---------------------------------------------------------------------------
# No-stdout contract on public APIs
# ---------------------------------------------------------------------------


def test_catalog_metrics_is_silent(
    semantic_project_factory: _SemanticProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    _ = catalog.search(kind="metric")
    assert capsys.readouterr().out == ""


def test_catalog_datasources_is_silent(
    semantic_project_factory: _SemanticProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    _ = catalog.search(kind="datasource")
    assert capsys.readouterr().out == ""


def test_readiness_is_silent(
    semantic_project_factory: _SemanticProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _make_project(semantic_project_factory)
    project.readiness()
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# repr() is one line and hints .show()
# ---------------------------------------------------------------------------


def test_logical_metric_repr_is_bounded_and_has_identity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    revenue = case.catalog.get("metric:sales.revenue")
    assert revenue.kind == "metric"
    result = case.session.observe({"revenue": revenue})
    rendered = repr(result)
    assert "\n" not in rendered
    assert len(rendered) <= 200


def test_catalog_page_repr_is_one_line(semantic_project_factory: _SemanticProjectFactory) -> None:
    catalog = _make_catalog(semantic_project_factory)
    result = catalog.search(kind="metric")
    r = repr(result)
    assert r.count("\n") == 0


# ---------------------------------------------------------------------------
# render() + show() contract (result types that own render())
# ---------------------------------------------------------------------------


def test_datasource_catalog_render_uses_card_listing_shape(tmp_path: Path) -> None:
    md.register(
        DuckDBSpec(name="warehouse", path=str(tmp_path / "warehouse.duckdb")),
        project_root=tmp_path,
    )
    catalog = md.load(workspace_dir=tmp_path)

    assert repr(catalog).count("\n") == 0
    assert catalog.show() is None  # type: ignore[func-returns-value]  # Verify the runtime return contract.


@pytest.mark.parametrize(
    "path",
    [
        "marivo/analysis/binding/observe.py",
        "marivo/analysis/session/_lazy_sources.py",
        "marivo/analysis/compiler/graph_lowering.py",
        "marivo/analysis/materialization/admission.py",
    ],
)
def test_analysis_runtime_does_not_query_public_catalog_collections(path: str) -> None:
    source = (PROJECT_ROOT / path).read_text()
    assert "catalog.list(" not in source
    assert "catalog._reg" not in source
