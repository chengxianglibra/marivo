"""Tests for the remaining marivo.semantic.reader SemanticProject lifecycle."""

from __future__ import annotations

import textwrap

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")


_OBJECTS_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))

    @ms.metric(
        entities=[orders],
        additivity="additive",
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
