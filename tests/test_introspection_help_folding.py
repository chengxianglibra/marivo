"""Pin the top-level help fold partition for each surface.

The old JSON ``_surface()`` infrastructure was removed in Phase 3 for both
the semantic and analysis surfaces.  The semantic surface now uses the
live capability-registry renderer (``ms.help_text()``).  This file retains
catalog-level and analysis-surface regression tests that do not depend on
the removed ``_surface`` function.
"""

from __future__ import annotations

_MINIMAL_DOMAIN_PY = (
    "import marivo.datasource as md\n"
    "import marivo.semantic as ms\n"
    'ms.domain(name="sales", owner="Mina Zhang", default=True)\n'
)
_DATASETS_PY = (
    "import marivo.datasource as md\n"
    "import marivo.semantic as ms\n"
    'orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), '
    'source=md.table("orders"))\n'
    "\n"
    "@ms.metric(entities=[orders], additivity='additive', )\n"
    "def revenue(table):\n"
    "    return table.amount.sum()\n"
)


def _make_catalog(semantic_project_factory):
    from marivo.semantic.catalog import SemanticCatalog

    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


def test_semantic_catalog_has_no_legacy_list_method(semantic_project_factory) -> None:
    catalog = _make_catalog(semantic_project_factory)

    assert not hasattr(catalog, "list")


def test_analysis_no_longer_uses_json_surface() -> None:
    """The analysis surface has moved to the capability-registry-based renderer.

    ``_surface`` is no longer available on ``marivo.analysis.help``; this test
    pins that the old JSON Surface infrastructure is gone for analysis.
    """
    import pytest

    from marivo.analysis import help as analysis_help
    from marivo.analysis import help_text as mv_help_text

    assert not hasattr(analysis_help, "_surface")

    # The new help system does not accept format= or json= kwargs.
    with pytest.raises(TypeError):
        mv_help_text("observe", format="json")  # type: ignore[call-arg]


def test_semantic_help_no_longer_uses_json_surface() -> None:
    """The semantic surface has moved to the capability-registry-based renderer.

    ``_surface`` is no longer available on ``marivo.semantic.help``.
    """
    from marivo.semantic import help as semantic_help

    assert not hasattr(semantic_help, "_surface")
