"""Source-free disclosure of contribution time on Metric expressions."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
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


def _reject_source_io(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("Metric display read the business datasource")


def test_named_and_anonymous_metrics_disclose_their_captured_branch_clocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    inherited = catalog_get(catalog, "metric:sales.inherited", kind="metric")
    created = catalog_get(catalog, "metric:sales.created", kind="metric")
    named = catalog_get(catalog, "metric:sales.difference", kind="metric")
    anonymous = inherited - created
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    monkeypatch.setattr(DatasourceConnectionService, "source_session", _reject_source_io)
    for result in (named, anonymous):
        output = result.render(max_output_bytes=None)
        assert "operand[0]" in output
        assert "operand[1]" in output
        assert "time_dimension:sales.orders.paid_at (entity_default)" in output
        assert "time_dimension:sales.orders.created_at (explicit)" in output
        assert "clocked branches follow report time" in output
    assert len(named.definition.time_bindings) == 2


def test_time_binding_omissions_are_counted_and_full_display_remains_available(
    tmp_path: Path,
) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    repeated = catalog_get(catalog, "metric:sales.repeated", kind="metric")
    bounded = repeated.render()
    assert len(repeated.definition.time_bindings) == 7
    assert "limited to 6" in bounded
    assert "omitted" in bounded
    assert ".definition.time_bindings" in bounded
    assert (
        repeated.render(max_output_bytes=None).count(
            "time_dimension:sales.orders.paid_at (entity_default)"
        )
        == 7
    )


def test_missing_clock_is_distinct_from_unresolved_authoring_symbol(tmp_path: Path) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    untimed = catalog_get(catalog, "metric:sales.untimed", kind="metric")
    assert "entity:sales.customers (report independent: no_entity_default)" in untimed.render()
    assert "independent branches retain their scope" in untimed.render()

    reference = ms.ref.metric("sales.untimed")

    @ms.metric()
    def unresolved() -> Deferred:
        return reference + 1

    assert "unknown: resolve the authoring symbols" in unresolved.render()


def test_expression_display_does_not_refresh_same_path_definitions(tmp_path: Path) -> None:
    _project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    first = catalog_get(catalog, "metric:sales.inherited", kind="metric")
    second = catalog_get(catalog, "metric:sales.created", kind="metric")
    expression = first - second
    before = expression.render(max_output_bytes=None)
    model = tmp_path / "models/semantic/sales/objects.py"
    model.write_text(model.read_text().replace('unit="USD"', 'unit="EUR"') + "\n# changed\n")
    assert expression.render(max_output_bytes=None) == before


def test_expression_display_marks_receiving_scope_required_for_changed_units(
    tmp_path: Path,
) -> None:
    _project(tmp_path)
    old = ms.load(workspace_dir=tmp_path)
    first = catalog_get(old, "metric:sales.inherited", kind="metric")
    model = tmp_path / "models/semantic/sales/objects.py"
    model.write_text(model.read_text().replace('unit="USD"', 'unit="EUR"') + "\n# changed\n")
    current = ms.load(workspace_dir=tmp_path)
    second = catalog_get(current, "metric:sales.created", kind="metric")
    output = (first - second).render()
    assert "unknown: receiving scope needed" in output
    assert "incommensurable_linear_units" in output
    assert "time_inspection_repair:" in output


def test_fixed_population_count_is_shown_as_untimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("metric-display", timezone="UTC")
    orders = catalog_get(session.catalog, "entity:sales.orders", kind="entity")
    count = session.members(orders).count()
    monkeypatch.setattr(DatasourceConnectionService, "backend_for", _reject_source_io)
    monkeypatch.setattr(DatasourceConnectionService, "source_session", _reject_source_io)
    assert "fixed Population.count (report independent)" in count.render()
