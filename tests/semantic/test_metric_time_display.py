"""Source-free disclosure of contribution time on Metric expressions."""

from __future__ import annotations

from pathlib import Path

import marivo.semantic as ms
from tests.shared_fixtures import catalog_get, write_minimal_catalog_project


def _project(path: Path) -> None:
    write_minimal_catalog_project(path)
    model = path / "models/semantic/sales/objects.py"
    text = model.read_text().replace(
        'source=md.table("orders"), primary_key=["order_id"])',
        'source=md.table("orders"), primary_key=["order_id"], '
        'default_time=ms.ref.time_dimension("sales.orders.paid_at"))',
    )
    model.write_text(
        text
        + """
created_at = ms.time_dimension_column(name="created_at", entity=orders, column="created_at")
customers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"),
    source=md.table("customers"), primary_key=["customer_id"])

@ms.metric()
def inherited():
    return amount.sum()

@ms.metric()
def created():
    return amount.sum(time=created_at)

@ms.metric()
def difference():
    return inherited - created

@ms.metric()
def repeated():
    return inherited + inherited + inherited + inherited + inherited + inherited + inherited

@ms.metric()
def untimed():
    return customers.count()
"""
    )


def test_named_and_anonymous_metrics_disclose_their_captured_branch_clocks(
    tmp_path: Path,
) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    named = catalog_get(catalog, "metric:sales.difference", kind="metric")
    assert len(named.definition.time_bindings) == 2


def test_time_binding_omissions_are_counted_and_full_display_remains_available(
    tmp_path: Path,
) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    repeated = catalog_get(catalog, "metric:sales.repeated", kind="metric")
    assert len(repeated.definition.time_bindings) == 7
