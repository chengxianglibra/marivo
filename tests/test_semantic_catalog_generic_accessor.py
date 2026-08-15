"""Issue #80: SemanticCatalog generic kind accessor (items() / __getitem__).

Before this fix, programmatic traversal had to hand-write a ``SemanticKind`` ->
property-name mapping because ``SemanticCatalog`` exposed one named property per
kind (``.metrics``, ``.dimensions``, ...) but no accessor keyed by the
``SemanticKind`` enum itself.  These tests lock ``catalog.items(kind)`` and
``catalog[kind]`` as the generic, kind-keyed entry that returns the same
``CatalogCollection`` as the matching named property.
"""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo.refs import SemanticKind
from marivo.semantic.catalog import CatalogCollection


def _write_minimal_project(tmp_path) -> None:
    semantic = tmp_path / "models" / "semantic" / "sales"
    ds = tmp_path / "models" / "datasources"
    semantic.mkdir(parents=True)
    ds.mkdir(parents=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(table):\n"
        "    return table.amount.sum()\n"
    )


def test_items_returns_collection_for_kind(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    collection = catalog.items(SemanticKind.METRIC)
    assert isinstance(collection, CatalogCollection)
    assert collection.refs == catalog.metrics.refs


def test_items_accepts_string_kind(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    assert catalog.items("metric").refs == catalog.metrics.refs


def test_getitem_is_alias_for_items(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    assert catalog[SemanticKind.DOMAIN].refs == catalog.domains.refs


def test_items_iterates_every_kind_without_crash(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    for kind in SemanticKind:
        assert isinstance(catalog.items(kind), CatalogCollection)


def test_items_matches_named_property_for_every_kind(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    named = {
        SemanticKind.DOMAIN: catalog.domains,
        SemanticKind.DATASOURCE: catalog.datasources,
        SemanticKind.ENTITY: catalog.entities,
        SemanticKind.DIMENSION: catalog.dimensions,
        SemanticKind.MEASURE: catalog.measures,
        SemanticKind.TIME_DIMENSION: catalog.time_dimensions,
        SemanticKind.METRIC: catalog.metrics,
        SemanticKind.RELATIONSHIP: catalog.relationships,
        SemanticKind.EVENT: catalog.events,
        SemanticKind.STATE_MODEL: catalog.state_models,
        SemanticKind.PERIOD_CALENDAR: catalog.period_calendars,
        SemanticKind.TEMPORAL_SET: catalog.temporal_sets,
        SemanticKind.WORK_SCHEDULE: catalog.work_schedules,
    }
    for kind, collection in named.items():
        assert catalog.items(kind).refs == collection.refs


def test_items_rejects_unknown_kind(tmp_path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    with pytest.raises(ValueError):
        catalog.items("not_a_kind")
