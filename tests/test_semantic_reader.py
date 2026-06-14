"""Tests for the remaining marivo.semantic.reader SemanticProject lifecycle."""

from __future__ import annotations

import textwrap

from marivo.semantic.reader import SemanticProject

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")


_OBJECTS_PY = textwrap.dedent("""\
    import marivo.semantic as ms

    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(
        entities=[orders],
        additivity="additive",
        decomposition=ms.sum(),
    )
    def revenue(table):
        return table.amount.sum()
""")


def test_reader_project_loads_and_exposes_status(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _OBJECTS_PY,
        }
    )

    assert project.is_ready()
    assert project.errors() == ()
    assert project.warnings() == ()


def test_reader_project_no_longer_exposes_catalog_read_or_preview_surface() -> None:
    removed = {
        "list_domains",
        "list_datasources",
        "list_entities",
        "list_dimensions",
        "list_time_dimensions",
        "list_metrics",
        "list_relationships",
        "get_entity",
        "get_metric",
        "materialize_dataset",
        "materialize_field",
        "materialize_metric",
        "preview_dataset",
        "preview_field",
        "preview_metric",
    }

    for name in removed:
        assert not hasattr(SemanticProject, name), name
