"""The canonical catalog identity and direct typed semantic-object boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import marivo.semantic as ms
from marivo.semantic._deferred_semantics import consume_field, consume_metric
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from tests.shared_fixtures import write_minimal_catalog_project


def test_every_loaded_kind_has_one_immutable_canonical_entry(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    page = catalog.search()
    assert tuple(entry.id for entry in page.items) == tuple(
        sorted(entry.id for entry in page.items)
    )
    assert {entry.kind for entry in page.items} == {
        "domain",
        "datasource",
        "entity",
        "dimension",
        "time_dimension",
        "measure",
        "metric",
        "relationship",
    }
    for entry in page.items:
        assert catalog.get(entry.id) is entry
        assert entry.id == f"{entry.kind}:{entry.path}"
        assert len(repr(entry).encode()) < 200
        assert "show" in repr(entry)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            entry.name = "changed"
        with pytest.raises((FrozenInstanceError, AttributeError)):
            entry.new_attribute = 1
    assert not hasattr(catalog, "metrics")
    assert not hasattr(catalog, "describe")
    assert "CatalogCollection" not in ms.__all__


def test_object_kind_narrowing_exposes_direct_typed_operations(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    metric = catalog.get("metric:sales.revenue")
    assert metric.kind == "metric"
    assert isinstance(metric, ms.MetricExpression)
    assert consume_metric(metric).roots[0].ref.key == "entity:sales.orders"
    field = catalog.get("dimension:sales.orders.status")
    assert field.kind == "dimension"
    assert consume_field(field).reference.ref.key == field.id
    entity = catalog.get("entity:sales.orders")
    assert entity.kind == "entity"
    assert entity.primary_key == ("order_id",)
    assert not hasattr(catalog.get("domain:sales"), "value")
    assert not hasattr(catalog.get("datasource:warehouse"), "value")


@pytest.mark.parametrize(
    "identity", ["revenue", "sales.revenue", "metric:", "unknown:sales.revenue"]
)
def test_get_requires_a_canonical_kind_and_complete_path(tmp_path: Path, identity: str) -> None:
    write_minimal_catalog_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    with pytest.raises(SemanticRuntimeError):
        catalog.get(identity)


def test_get_preserves_requested_kind_and_relevant_repair(tmp_path: Path) -> None:
    write_minimal_catalog_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    with pytest.raises(SemanticRuntimeError) as wrong:
        catalog.get("measure:sales.orders.status")
    assert wrong.value.kind == ErrorKind.NOT_FOUND
    assert wrong.value.details["reason"] == "wrong_kind"
    assert wrong.value.repair is not None
    assert wrong.value.repair.candidates == ("dimension:sales.orders.status",)
    with pytest.raises(SemanticRuntimeError) as typo:
        catalog.get("metric:sales.reveneu")
    assert typo.value.repair is not None
    assert typo.value.repair.candidates[0] == "metric:sales.revenue"
