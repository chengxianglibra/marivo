"""Native identity selectors stay outside comparison, execution, and wire keys."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred
from pydantic import TypeAdapter

import marivo.datasource as md
import marivo.semantic as ms
from marivo._symbolic import native_symbol_ref
from marivo._temporal import Grain, _new_time_scope, semantic_grain
from marivo.refs import ref as internal_ref
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.preview_scope import _normalize_scopes
from tests.support.refs import as_ref


def test_source_checks_extract_only_native_keys_and_keep_value_identity() -> None:
    field = ms.ref.measure("sales.orders.amount")
    check = ms.source_check.not_null(field)
    same = ms.source_check.not_null(ms.ref.measure("sales.orders.amount"))
    assert check == same
    assert hash(check) == hash(same)
    assert native_symbol_ref(check.field) == internal_ref.measure("sales.orders.amount")
    assert asdict(check)["_field"] == internal_ref.measure("sales.orders.amount")
    for invalid in (
        field + 1,
        ms.ref.entity("sales.orders"),
        internal_ref.measure("sales.orders.amount"),
    ):
        with pytest.raises(TypeError, match="native lookup symbol"):
            ms.source_check.not_null(invalid)
    with pytest.raises(ValueError, match="must not repeat"):
        ms.source_check.unique(fields=(field, ms.ref.measure("sales.orders.amount")))
    freshness = ms.source_check.freshness(
        ms.ref.time_dimension("sales.orders.ts"), max_age=timedelta(days=1)
    )
    assert native_symbol_ref(freshness.field) == internal_ref.time_dimension("sales.orders.ts")


def test_preview_pairs_validate_keys_duplicates_and_exact_coverage() -> None:
    orders = ms.ref.entity("sales.orders")
    customers = ms.ref.entity("sales.customers")
    scope = md.unpruned(max_rows=3, timeout_seconds=5)
    normalized = _normalize_scopes(
        ("sales.orders", "sales.customers"),
        ((customers, scope), (orders, scope)),
        preview_ref="sales.link",
    )
    assert normalized == (("sales.orders", scope), ("sales.customers", scope))
    invalid_pairs = (
        ((orders, scope), (ms.ref.entity("sales.orders"), scope)),
        ((orders, scope),),
        ((orders, scope), (customers, scope), (ms.ref.entity("sales.extra"), scope)),
        ((internal_ref.entity("sales.orders"), scope), (customers, scope)),
        {internal_ref.entity("sales.orders"): scope, internal_ref.entity("sales.customers"): scope},
    )
    for invalid in invalid_pairs:
        with pytest.raises(SemanticRuntimeError):
            _normalize_scopes(
                ("sales.orders", "sales.customers"), invalid, preview_ref="sales.link"
            )
    assert _normalize_scopes(("sales.orders",), scope, preview_ref="sales.orders") == (
        ("sales.orders", scope),
    )
    with pytest.raises(SemanticRuntimeError, match="single-entity"):
        _normalize_scopes(("sales.orders",), ((orders, scope),), preview_ref="sales.orders")


def test_temporal_navigation_preserves_identity_and_wire() -> None:
    calendar = internal_ref.period_calendar("sales.fiscal")
    grain = semantic_grain(calendar=calendar, level="week")
    same = semantic_grain(calendar=calendar, level="week")
    assert isinstance(grain.calendar, Deferred)
    assert native_symbol_ref(grain.calendar) == calendar
    assert grain == same and hash(grain) == hash(same)
    adapter = TypeAdapter(Grain)
    wire = adapter.dump_python(grain, mode="json")
    assert wire["calendar"] == {
        "schema": "marivo.semantic_ref/v1",
        "kind": "period_calendar",
        "path": "sales.fiscal",
    }
    scope = _new_time_scope(
        start=date(2026, 1, 1),
        end=date(2026, 1, 8),
        calendar=calendar,
        snapshot_digest="frozen",
        boundary_timezone="UTC",
        level="week",
        key=1,
        ordinal=0,
    )
    assert native_symbol_ref(scope.calendar) == calendar
    encoded = scope._model_dump(mode="json")
    assert encoded["calendar"] == wire["calendar"]
    assert "calendar_identity" not in encoded
    assert hash(scope) == hash(
        _new_time_scope(
            start=date(2026, 1, 1),
            end=date(2026, 1, 8),
            calendar=calendar,
            snapshot_digest="frozen",
            boundary_timezone="UTC",
            level="week",
            key=1,
            ordinal=0,
        )
    )


def test_catalog_navigation_is_reusable_without_deferred_comparison(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": 'import marivo.datasource as md\nmd.duckdb(name="warehouse", path=":memory:")\n',
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Data", default=True)\n',
            "sales/orders.py": 'import marivo.semantic as ms\nimport marivo.datasource as md\norders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["id"])\namount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")\n',
        }
    )
    catalog = SemanticCatalog(project)
    entry = catalog.get("domain:sales")
    assert entry == catalog.get("domain:sales")
    assert hash(entry) == hash(catalog.get("domain:sales"))
    assert native_symbol_ref(entry.ref) == internal_ref.domain("sales")
    assert isinstance(entry.ref, Deferred)
    selected = catalog.search(kind="measure").items[0]
    selector = selected.ref
    assert native_symbol_ref(selector) == internal_ref.measure("sales.orders.amount")
    assert "amount" in catalog.get(as_ref(selector).key).render()
    ready = catalog.readiness(refs=[selector])
    assert native_symbol_ref(ready.analysis_ready_inputs[0]) == internal_ref.measure(
        "sales.orders.amount"
    )
    assert ready.to_dict()["analysis_ready_inputs"] == [
        {"schema": "marivo.semantic_ref/v1", "kind": "measure", "path": "sales.orders.amount"}
    ]
    for invalid in (selector + 1, internal_ref.measure("sales.orders.amount"), selector):
        with pytest.raises(SemanticRuntimeError):
            catalog.get(invalid)


def test_datasource_selectors_reject_internal_keys_before_inspection(tmp_path: Path) -> None:
    spec = md.duckdb(name="warehouse", path=":memory:")
    assert native_symbol_ref(spec.ref) == internal_ref.datasource("warehouse")
    for invalid in (
        internal_ref.datasource("warehouse"),
        ms.ref.entity("sales.orders"),
        spec.ref + 1,
    ):
        with pytest.raises(TypeError, match="native datasource lookup symbol"):
            md.inspect(invalid, md.table("orders"), workspace_dir=tmp_path)
        with pytest.raises(TypeError, match="native datasource lookup symbol"):
            md.raw_sql(
                invalid, "SELECT 1", reason="native selector boundary", project_root=tmp_path
            )
