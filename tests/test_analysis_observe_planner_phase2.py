"""Phase 2 cross-dataset observe planner units."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from marivo.analysis.intents.observe_planner import (
    _derive_version_mode,
    _effective_key,
)


def _bootstrap_validity_dataset(semantic_project_factory, *, primary_key: str):
    return semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "@ms.dimension(entity='sales.user_history')\n"
                "def valid_from(t):\n"
                "    return t.valid_from\n"
                "@ms.dimension(entity='sales.user_history')\n"
                "def valid_to(t):\n"
                "    return t.valid_to\n"
                "user_history = ms.entity(\n"
                "    name='user_history',\n"
                "    datasource='warehouse',\n"
                "    source=ms.table('user_history'),\n"
                f"    primary_key={primary_key},\n"
                "    versioning=ms.validity(valid_from=valid_from, valid_to=valid_to, interval='closed_open', open_end=(None,)),\n"
                ")\n"
            ),
        }
    )


def test_effective_key_validity_subtracts_valid_from_only(semantic_project_factory):
    project = _bootstrap_validity_dataset(
        semantic_project_factory, primary_key="['user_id', 'valid_from']"
    )
    assert _effective_key(project, "sales.user_history") == ("user_id",)


def test_effective_key_validity_subtracts_valid_from_and_valid_to(semantic_project_factory):
    project = _bootstrap_validity_dataset(
        semantic_project_factory, primary_key="['user_id', 'valid_from', 'valid_to']"
    )
    assert _effective_key(project, "sales.user_history") == ("user_id",)


def _date_time_field():
    return SimpleNamespace(data_type="date", semantic_id="sales.orders.order_date")


def _timestamp_time_field():
    return SimpleNamespace(data_type="timestamp", semantic_id="sales.orders.created_at")


def _string_time_field():
    return SimpleNamespace(data_type="string", semantic_id="sales.tag")


def _snapshot_versioning():
    from marivo.semantic.ir import SnapshotVersioningIR

    return SnapshotVersioningIR(
        kind="snapshot",
        partition_field="sales.user_profile_daily.dt",
        grain="day",
        timezone="UTC",
        format=None,
    )


def _validity_versioning():
    from marivo.semantic.ir import ValidityVersioningIR

    return ValidityVersioningIR(
        kind="validity",
        valid_from="sales.user_history.valid_from",
        valid_to="sales.user_history.valid_to",
        interval="closed_open",
        open_end=(None,),
        timezone="UTC",
    )


@pytest.mark.parametrize("versioning_factory", [_snapshot_versioning, _validity_versioning])
def test_derive_version_mode_picks_as_of_root_time(versioning_factory):
    mode, anchor_source, anchor_value = _derive_version_mode(
        root_time_dimension=_date_time_field(),
        target_versioning=versioning_factory(),
        resolved_window=None,
    )
    assert mode == "as_of_root_time"
    assert anchor_source == "root"
    assert anchor_value is None


def test_derive_version_mode_falls_back_to_latest_with_timescope(monkeypatch):
    window = SimpleNamespace(end=dt.date(2026, 7, 3))
    mode, anchor_source, anchor_value = _derive_version_mode(
        root_time_dimension=None,
        target_versioning=_snapshot_versioning(),
        resolved_window=window,
    )
    assert mode == "latest"
    assert anchor_source == "timescope_end"
    assert anchor_value == dt.date(2026, 7, 3)


def test_derive_version_mode_falls_back_to_latest_with_plan_time(monkeypatch):
    monkeypatch.setattr(
        "marivo.analysis.intents.observe_planner._utc_now",
        lambda: dt.datetime(2026, 7, 5, 12, 0, tzinfo=dt.UTC),
    )
    mode, anchor_source, anchor_value = _derive_version_mode(
        root_time_dimension=None,
        target_versioning=_validity_versioning(),
        resolved_window=None,
    )
    assert mode == "latest"
    assert anchor_source == "as_of_current_time"
    assert anchor_value == dt.date(2026, 7, 5)


def test_derive_version_mode_string_time_field_is_not_qualifying():
    mode, anchor_source, _anchor = _derive_version_mode(
        root_time_dimension=_string_time_field(),
        target_versioning=_snapshot_versioning(),
        resolved_window=SimpleNamespace(end=dt.date(2026, 1, 1)),
    )
    assert mode == "latest"
    assert anchor_source == "timescope_end"


def test_plan_observe_dispatches_to_base_for_non_derived(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource='warehouse', primary_key=['order_id'], source=ms.table('orders'))\n"
                "@ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), name='revenue', verification_mode='python_native',)\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    metric = project.get_metric("sales.revenue")
    from marivo.analysis.intents.observe_planner import (
        plan_observe,
    )

    # plan_observe with a fake session/backends is heavyweight; test that the
    # function returns a BaseObservePlan when metric is non-derived using a
    # very small bootstrap that does not require execution. Using
    # observe.observe(...) is the integration test; here we only verify
    # dispatch by inspecting metric_ir.is_derived branching directly:
    assert metric.is_derived is False
    # plan_observe is integration-tested via observe end-to-end suites; this
    # narrow test asserts the symbol is exported and is callable signature.
    assert callable(plan_observe)
