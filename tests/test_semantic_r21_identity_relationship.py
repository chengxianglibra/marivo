"""Source-free R2.1 identity, version, and directed relationship contracts."""

from __future__ import annotations

from datetime import datetime

import pytest

import marivo.semantic as ms
from marivo.analysis.observation.coordinates import (
    functional_path,
)
from marivo.analysis.observation.errors import ObservationConstructionError
from marivo.semantic.catalog import RelationshipDetails, SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed
from marivo.semantic.ir import TargetSnapshotSelection
from marivo.semantic.validator import (
    normalize_target_entity,
    normalize_target_relationship,
    normalize_target_version_selection,
)

_DOMAIN = "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
_OBJECTS = """
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(
    name='orders', datasource=ms.ref.datasource('warehouse'),
    source=md.table('orders', columns={
        'tenant_id': 'tenant', 'order_id': 'number', 'snapshot_day': 'business_day',
    }),
    primary_key=['tenant_id', 'order_id'],
    versioning=ms.snapshot(
        partition_field=ms.ref.time_dimension('sales.orders.snapshot_day'),
        grain='day', timezone='UTC',
    ),
)
order_tenant = ms.dimension_column(name='tenant_id', entity=orders, column='tenant_id')
order_id = ms.dimension_column(name='order_id', entity=orders, column='order_id')
snapshot_day = ms.time_dimension_column(
    name='snapshot_day', entity=orders, column='snapshot_day', granularity='day',
    parse=ms.strptime('%Y-%m-%d'),
)

lines = ms.entity(
    name='lines', datasource=ms.ref.datasource('warehouse'),
    source=md.table('lines', columns={
        'tenant_id': 'tenant', 'line_id': 'line_number', 'order_id': 'order_number',
    }),
    primary_key=['tenant_id', 'line_id'],
)
line_tenant = ms.dimension_column(name='tenant_id', entity=lines, column='tenant_id')
line_order = ms.dimension_column(name='order_id', entity=lines, column='order_id')
line_id = ms.dimension_column(name='line_id', entity=lines, column='line_id')

line_to_order = ms.relationship(
    name='line_to_order', from_entity=lines, to_entity=orders,
    keys=[ms.join_on(line_tenant, order_tenant), ms.join_on(line_order, order_id)],
)
"""


def _project(semantic_project_factory, source: str = _OBJECTS):
    return semantic_project_factory({"sales/_domain.py": _DOMAIN, "sales/objects.py": source})


def test_composite_identity_and_versioned_relationship_are_declared_without_source_io(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory)
    catalog = SemanticCatalog(project)
    orders = normalize_target_entity(project._registry, "sales.orders")
    mapping = normalize_target_relationship(project._registry, "sales.line_to_order")

    assert orders.primary_key == ("tenant_id", "order_id")
    assert orders.version_row_key == ("tenant_id", "order_id", "snapshot_day")
    assert mapping.keys == (("tenant_id", "tenant_id"), ("order_id", "order_id"))
    assert mapping.cardinality == "many_to_one"
    assert mapping.role == "line_to_order"
    assert mapping.to_version_resolution_required is True
    assert mapping.from_version_resolution_required is False
    details = catalog.require(ms.ref.relationship("sales.line_to_order")).details()
    assert isinstance(details, RelationshipDetails)
    assert details.cardinality == "many_to_one"
    assert "structural_cardinality: many_to_one" in details.render()
    assert details.to_version_resolution_required is True
    with pytest.raises(ObservationConstructionError, match="versioned source or intermediate path"):
        functional_path(project._registry, "sales.lines", "sales.orders")
    selection = normalize_target_version_selection(
        orders, boundary=datetime(2026, 9, 27), interpretation="before_endpoint"
    )
    assert isinstance(selection, TargetSnapshotSelection)
    assert selection.period == "2026-09-26"


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "ms.join_on(line_order, order_id)",
            "ms.join_on(line_order, order_tenant)",
            "each endpoint key column used once",
        ),
        (
            "ms.join_on(line_order, order_id)",
            "ms.join_on(order_id, line_order)",
            "exact from/to Entity endpoints",
        ),
    ],
)
def test_invalid_relationship_keys_fail_at_load(
    semantic_project_factory, old: str, new: str, expected: str
) -> None:
    project = _project(semantic_project_factory, _OBJECTS.replace(old, new))
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project)
    assert any(
        error.kind == "invalid_relationship_mapping" and expected in error.expected
        for error in exc_info.value.errors
    )


def test_incomplete_identity_coverage_does_not_grant_functional_path(
    semantic_project_factory,
) -> None:
    source = _OBJECTS.replace(", ms.join_on(line_order, order_id)", "")
    project = _project(semantic_project_factory, source)
    SemanticCatalog(project)
    mapping = normalize_target_relationship(project._registry, "sales.line_to_order")
    assert mapping.cardinality == "many_to_many"
    with pytest.raises(ObservationConstructionError):
        functional_path(
            project._registry, "sales.lines", "sales.orders", allow_versioned_target=True
        )


def test_extra_join_condition_preserves_complete_target_key_coverage(
    semantic_project_factory,
) -> None:
    source = (
        _OBJECTS.replace(
            "'snapshot_day': 'business_day',",
            "'snapshot_day': 'business_day', 'region_id': 'number',",
        )
        .replace(
            "'tenant_id': 'tenant', 'line_id': 'line_number', 'order_id': 'order_number',",
            "'tenant_id': 'tenant', 'line_id': 'line_number', 'order_id': 'order_number', 'region_id': 'number',",
        )
        .replace(
            "line_to_order = ms.relationship(",
            "order_region = ms.dimension_column(name='region_id', entity=orders, column='region_id')\n"
            "line_region = ms.dimension_column(name='region_id', entity=lines, column='region_id')\n"
            "line_to_order = ms.relationship(",
        )
        .replace(
            "ms.join_on(line_order, order_id)]",
            "ms.join_on(line_order, order_id), ms.join_on(line_region, order_region)]",
        )
    )
    project = _project(semantic_project_factory, source)
    SemanticCatalog(project)
    mapping = normalize_target_relationship(project._registry, "sales.line_to_order")
    assert mapping.cardinality == "many_to_one"


def test_role_choice_and_fanout_are_not_inferred_from_join_success(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory)
    registry = project._registry
    assert functional_path(
        registry, "sales.lines", "sales.orders", allow_versioned_target=True
    ) == ("sales.line_to_order",)
    with pytest.raises(ObservationConstructionError):
        functional_path(registry, "sales.orders", "sales.lines", allow_versioned_source=True)

    second = (
        _OBJECTS
        + "\nalternate_order = ms.relationship(\n"
        + (
            "    name='alternate_order', from_entity=lines, to_entity=orders,\n"
            "    keys=[ms.join_on(line_tenant, order_tenant), ms.join_on(line_order, order_id)],\n"
            ")\n"
        )
    )
    ambiguous = _project(semantic_project_factory, second)
    assert SemanticCatalog(ambiguous).relationships.get("alternate_order") is not None
    with pytest.raises(ObservationConstructionError, match="ambiguous"):
        functional_path(
            ambiguous._registry, "sales.lines", "sales.orders", allow_versioned_target=True
        )


def test_nonversioned_duplicate_identity_declaration_fails_at_load(
    semantic_project_factory,
) -> None:
    source = _OBJECTS.replace(
        "primary_key=['tenant_id', 'line_id']", "primary_key=['tenant_id', 'tenant_id']"
    )
    project = _project(semantic_project_factory, source)
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project)
    assert any(error.kind == "duplicate_identity_key" for error in exc_info.value.errors)


def test_identity_key_requires_concrete_column_names(semantic_project_factory) -> None:
    source = _OBJECTS.replace("primary_key=['tenant_id', 'line_id']", "primary_key='tenant_id'")
    project = _project(semantic_project_factory, source)
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project)
    assert any(error.kind == "invalid_ref" for error in exc_info.value.errors)
