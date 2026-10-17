"""SemanticCatalog named member collections.

``SemanticCatalog`` exposes exactly one named collection property per member
contract in ``catalog_members.py``. These tests lock the closed member contract
against the live catalog: every declared property exists, returns a
``CatalogCollection`` restricted to the declared kinds, and resolves its
members by local name, full path, typed key, and exact Ref.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.catalog_members import (
    CATALOG_MEMBER_CONTRACTS,
)
from marivo.semantic._deferred_semantics import consume_field
from marivo.semantic.catalog import CatalogCollection

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", owner="Mina Zhang", default=True)
"""

_OBJECTS_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(
    name="orders",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"),
    primary_key=["order_id"],
)
status = ms.dimension_column(name="status", entity=orders, column="status")
paid_at = ms.time_dimension_column(
    name="paid_at",
    entity=orders,
    column="paid_at",
    granularity="day",
    parse=ms.timestamp(timezone="UTC"),
)
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity=ms.additive_all()
)
ms.relationship(
    name="orders_self",
    from_entity=orders,
    to_entity=orders,
    keys=[ms.join_on(status, status)],
)

finalized = ms.finalize()
amount_value = finalized.resolve(amount)
paid_at_value = finalized.resolve(paid_at)


@ms.metric()
def revenue():
    return amount_value.sum(time=paid_at_value)
"""

# Declared members present in the fixture project, in displayed key order.
_EXPECTED_MEMBER_KEYS: dict[str, tuple[str, ...]] = {
    "domains": ("domain:sales",),
    "entities": ("entity:sales.orders",),
    "fields": (
        "dimension:sales.orders.status",
        "measure:sales.orders.amount",
        "time_dimension:sales.orders.paid_at",
    ),
    "metrics": ("metric:sales.revenue",),
    "relationships": ("relationship:sales.orders_self",),
    "period_calendars": (),
    "temporal_sets": (),
    "work_schedules": (),
}


def _write_minimal_project(tmp_path: Path) -> None:
    semantic = tmp_path / "models" / "semantic" / "sales"
    ds = tmp_path / "models" / "datasources"
    semantic.mkdir(parents=True)
    ds.mkdir(parents=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(_DOMAIN_PY)
    (semantic / "objects.py").write_text(textwrap.dedent(_OBJECTS_PY))


def test_every_member_property_is_a_typed_collection_of_its_declared_kinds(
    tmp_path: Path,
) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)

    for member in CATALOG_MEMBER_CONTRACTS:
        collection = getattr(catalog, member.property_name)
        assert isinstance(collection, CatalogCollection)
        # The collection holds only the kinds its contract declares.
        assert {ref.kind for ref in collection.refs} <= set(member.kinds)
        assert len(collection) == len(collection.refs)
        if collection.refs:
            assert member.item_type_name in collection.render()


def test_every_member_property_lists_its_declared_members_in_key_order(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)

    for property_name, expected_keys in _EXPECTED_MEMBER_KEYS.items():
        collection = getattr(catalog, property_name)
        assert tuple(ref.key for ref in collection.refs) == expected_keys

    assert "datasource:warehouse" in {ref.key for ref in catalog.datasources.refs}
    assert {ref.kind for ref in catalog.datasources.refs} == {SemanticKind.DATASOURCE}


def test_every_member_value_round_trips_through_get_by_name_path_key_and_ref(
    tmp_path: Path,
) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)

    for member in CATALOG_MEMBER_CONTRACTS:
        collection = getattr(catalog, member.property_name)
        for ref, item in zip(collection.refs, collection.items, strict=True):
            assert (
                (consume_field(collection.get(ref.key)) == consume_field(item))
                if isinstance(item, Deferred) and ref.kind is not SemanticKind.METRIC
                else collection.get(ref.key) is item
            )
            assert (
                (consume_field(collection.get(ref.path)) == consume_field(item))
                if isinstance(item, Deferred) and ref.kind is not SemanticKind.METRIC
                else collection.get(ref.path) is item
            )
            assert (
                (consume_field(collection.get(ref)) == consume_field(item))
                if isinstance(item, Deferred) and ref.kind is not SemanticKind.METRIC
                else collection.get(ref) is item
            )
        names = [ref.name for ref in collection.refs]
        if len(set(names)) == len(names):
            for ref, item in zip(collection.refs, collection.items, strict=True):
                assert (
                    (consume_field(collection.get(ref.name)) == consume_field(item))
                    if isinstance(item, Deferred) and ref.kind is not SemanticKind.METRIC
                    else collection.get(ref.name) is item
                )


def test_fields_collection_spans_dimension_time_field_and_measure(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)

    fields = catalog.fields
    assert [ref.kind.value for ref in fields.refs] == [
        "dimension",
        "measure",
        "time_dimension",
    ]
    for ref in fields.refs:
        assert consume_field(fields.get(ref.key)).reference.ref == ref


def test_scoped_collection_matches_the_global_collection(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)

    sales = catalog.domains.get("sales")

    assert sales.metrics.refs == catalog.metrics.refs
    assert sales.fields.refs == catalog.fields.refs
    assert sales.metrics.get("sales.revenue") is catalog.metrics.get("sales.revenue")
