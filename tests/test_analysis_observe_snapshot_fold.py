"""Entity-first observe execution for unsampled snapshot semi-additive metrics."""

from __future__ import annotations

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.intents.observe_errors import ObservePlanningError


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _bootstrap_snapshot_project(
    tmp_path,
    *,
    identity: bool = True,
    duplicate_latest: bool = False,
    versioning: bool = True,
    granularity: str = "day",
):
    semantic_dir = tmp_path / "models" / "semantic" / "inventory"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    primary_key = "['snapshot_date', 'product_id']" if identity else "['snapshot_date']"
    versioning_line = (
        "    versioning=ms.snapshot(partition_field=snapshot_date, grain='day'),\n"
        if versioning
        else ""
    )
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "ms.domain(name='inventory', owner='Marivo')\n"
        "snapshots_ref = ms.ref.entity('inventory.snapshots')\n"
        "snapshot_date = ms.time_dimension_column(\n"
        "    name='snapshot_date', entity=snapshots_ref, column='snapshot_date',\n"
        f"    granularity='{granularity}', is_default=True,\n"
        ")\n"
        "snapshots = ms.entity(\n"
        "    name='snapshots', datasource=ms.ref.datasource('warehouse'),\n"
        "    source=md.table('snapshots'),\n"
        f"    primary_key={primary_key},\n"
        f"{versioning_line}"
        ")\n"
        "product_id = ms.dimension_column(\n"
        "    name='product_id', entity=snapshots, column='product_id',\n"
        ")\n"
        "category = ms.dimension_column(\n"
        "    name='category', entity=snapshots, column='category',\n"
        ")\n"
        "@ms.measure(\n"
        "    entity=snapshots,\n"
        "    additivity=ms.semi_additive(over=snapshot_date, fold='last'),\n"
        "    unit='{item}',\n"
        ")\n"
        "def quantity_on_hand(snapshots):\n"
        "    return snapshots.quantity_on_hand\n"
        "end_inventory = ms.aggregate(\n"
        "    name='end_inventory', measure=quantity_on_hand, agg='sum', fold='last',\n"
        ")\n"
        "start_inventory = ms.aggregate(\n"
        "    name='start_inventory', measure=quantity_on_hand, agg='sum', fold='first',\n"
        ")\n"
        "average_inventory = ms.aggregate(\n"
        "    name='average_inventory', measure=quantity_on_hand, agg='sum', fold='mean',\n"
        ")\n"
        "inventory_ratio = ms.ratio(\n"
        "    name='inventory_ratio', numerator=end_inventory, denominator=start_inventory,\n"
        ")\n"
    )
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE snapshots ("
        "snapshot_date DATE, product_id INTEGER, category VARCHAR, quantity_on_hand DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO snapshots VALUES "
        "(DATE '2026-01-01', 1, 'A', 10), "
        "(DATE '2026-01-01', 2, 'B', 20), "
        "(DATE '2026-01-02', 1, 'A', 15), "
        "(DATE '2026-01-03', 2, 'B', 25)"
    )
    if duplicate_latest:
        con.raw_sql("INSERT INTO snapshots VALUES (DATE '2026-01-02', 1, 'A', 5)")
    session = session_attach.get_or_create(
        name="snapshot-fold",
        backends={"warehouse": lambda: con},
    )
    return session


def test_snapshot_last_selects_per_entity_before_scalar_aggregation(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)

    frame = session.observe(ms.ref.metric("inventory.end_inventory"))

    assert frame.to_pandas().to_dict(orient="records") == [{"end_inventory": 40.0}]
    assert frame.meta.fold == {
        "time_fold": "last",
        "fold_kind": "last",
        "status_time_dimension": "inventory.snapshots.snapshot_date",
        "sample_interval": None,
        "fold_strategy": "snapshot_selection",
        "identity_keys": ["product_id"],
    }
    assert frame.meta.coverage_ref is None
    rendered = frame.render()
    assert "observation_scope: all available rows" in rendered
    assert "aggregation=sum" in rendered
    assert "additivity=semi_additive" in rendered
    assert "time_fold=last" in rendered
    assert "fold_strategy=snapshot_selection" in rendered
    assert "sample_interval=none" in rendered
    assert "identity_keys=[product_id]" in rendered
    assert "expected_sample_coverage: not_applicable" in rendered

    restored = session.get_frame(frame.ref)
    assert "fold_strategy=snapshot_selection" in restored.render()
    assert "identity_keys=[product_id]" in restored.render()


def test_snapshot_last_preserves_segment_dimensions(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)

    frame = session.observe(
        ms.ref.metric("inventory.end_inventory"),
        dimensions=[ms.ref.dimension("inventory.snapshots.category")],
    )

    assert frame.to_pandas().to_dict(orient="records") == [
        {"category": "A", "end_inventory": 15.0},
        {"category": "B", "end_inventory": 25.0},
    ]


def test_snapshot_last_selects_inside_each_time_bucket(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)

    frame = session.observe(
        ms.ref.metric("inventory.end_inventory"),
        time_scope=mv.time_scope(start="2026-01-01", end="2026-01-04"),
        grain=mv.grain("day"),
    )

    values = frame.to_pandas()["end_inventory"].tolist()
    assert values == [30.0, 15.0, 25.0]


def test_snapshot_first_selects_per_entity(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)

    frame = session.observe(ms.ref.metric("inventory.start_inventory"))

    assert frame.to_pandas().to_dict(orient="records") == [{"start_inventory": 30.0}]
    assert frame.meta.fold is not None
    assert frame.meta.fold["fold_strategy"] == "snapshot_selection"


def test_snapshot_selection_retains_duplicate_rows_at_selected_time(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path, duplicate_latest=True)

    frame = session.observe(ms.ref.metric("inventory.end_inventory"))

    assert frame.to_pandas().to_dict(orient="records") == [{"end_inventory": 45.0}]


def test_snapshot_fold_derived_metric_leaf_uses_same_strategy(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)

    frame = session.observe(ms.ref.metric("inventory.inventory_ratio"))

    assert frame.to_pandas()["inventory_ratio"].item() == pytest.approx(40 / 30)
    assert frame.meta.fold is not None
    component_folds = frame.meta.fold["component_folds"]
    assert {item["fold_strategy"] for item in component_folds} == {"snapshot_selection"}
    rendered = frame.render()
    assert "component folds:" in rendered
    assert "fold_strategy=snapshot_selection" in rendered
    assert "expected_sample_coverage: not_applicable" in rendered


def test_runtime_linear_does_not_inherit_semi_additive_bucket(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)
    end_inventory = session.catalog.require(ms.ref.metric("inventory.end_inventory")).ref
    start_inventory = session.catalog.require(ms.ref.metric("inventory.start_inventory")).ref

    frame = session.observe(
        mv.runtime_metric.linear(
            add=[end_inventory],
            subtract=[start_inventory],
            label="Inventory change",
        )
    )

    assert frame.to_pandas()["Inventory change"].item() == pytest.approx(10.0)
    assert frame.meta.additivity == "non_additive"
    assert frame.meta.status_time_dimension is None
    assert frame.meta.lineage.steps[0].params["metric_semantics"] == {
        "additivity": "non_additive",
        "aggregation": None,
        "status_time_dimension_ref": None,
    }
    component_graph = frame.components().meta.component_graph
    assert component_graph is not None
    root = next(
        node
        for node in component_graph["nodes"]
        if node["node_id"] == frame.meta.expression_graph.roots[0]
    )
    assert root["value_semantics"]["additivity"] == "non_additive"


def test_snapshot_fold_empty_window_returns_empty_frame_without_assertion(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)

    frame = session.observe(
        ms.ref.metric("inventory.end_inventory"),
        time_scope=mv.time_scope(start="2027-01-01", end="2027-01-02"),
        grain=mv.grain("day"),
    )

    assert frame.to_pandas().empty


def test_unsampled_non_selection_fold_fails_with_structured_error(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path)

    with pytest.raises(ObservePlanningError) as captured:
        session.observe(ms.ref.metric("inventory.average_inventory"))

    context = captured.value._context
    assert context["code"] == "unsampled-time-fold-unsupported"
    # At day grain no sample_interval can be declared, so the only repair is the
    # selection-fold switch (versioning is already bound).
    assert [repair["action"] for repair in context["repair"]] == ["use_first_last_fold"]
    assert all(repair["safety"] == "modeling_decision" for repair in context["repair"])


def test_snapshot_fold_requires_business_entity_identity(tmp_path) -> None:
    session = _bootstrap_snapshot_project(tmp_path, identity=False)

    with pytest.raises(ObservePlanningError) as captured:
        session.observe(ms.ref.metric("inventory.end_inventory"))

    context = captured.value._context
    assert context["code"] == "snapshot-fold-identity-missing"
    assert [repair["action"] for repair in context["repair"]] == ["declare_entity_identity"]
    assert context["repair"][0]["target"] == "inventory.snapshots"
    assert context["repair"][0]["value"] == "<business_identity_columns>"
    assert context["candidates"]["available_identity_columns"] == ["category", "product_id"]


def test_snapshot_fold_deadlock_when_no_versioning_and_no_sample_interval(tmp_path) -> None:
    """At day grain no sample_interval can be declared, so the first/last deadlock
    carries only the snapshot-versioning declaration."""
    session = _bootstrap_snapshot_project(tmp_path, versioning=False)

    with pytest.raises(ObservePlanningError) as captured:
        session.observe(ms.ref.metric("inventory.end_inventory"))

    context = captured.value._context
    assert context["code"] == "snapshot-fold-deadlock"
    assert [repair["action"] for repair in context["repair"]] == ["add_snapshot_versioning"]
    assert context["repair"][0]["target"] == "inventory.snapshots"
    assert context["repair"][0]["arg"] == "versioning"
    # The versioning value must be pasteable in the domain-authoring namespace
    # (local symbol, grain='day' is the only supported snapshot grain).
    assert (
        context["repair"][0]["value"] == "ms.snapshot(partition_field=snapshot_date, grain='day')"
    )


def test_snapshot_fold_deadlock_covers_non_selection_fold(tmp_path) -> None:
    """At day grain no sample_interval can be declared, so a non-selection fold
    deadlocks with the only path: switch to a selection fold (which then needs
    snapshot versioning)."""
    session = _bootstrap_snapshot_project(tmp_path, versioning=False)

    with pytest.raises(ObservePlanningError) as captured:
        session.observe(ms.ref.metric("inventory.average_inventory"))

    context = captured.value._context
    assert context["code"] == "snapshot-fold-deadlock"
    assert [repair["action"] for repair in context["repair"]] == [
        "use_first_last_fold",
        "add_snapshot_versioning",
    ]
    assert context["repair"][0]["value"] == "last"
    assert (
        context["repair"][1]["value"] == "ms.snapshot(partition_field=snapshot_date, grain='day')"
    )


def test_snapshot_fold_deadlock_non_day_grain_has_no_versioning_path(tmp_path) -> None:
    """Snapshot versioning only supports grain='day'; a non-day status time
    dimension cannot use the versioning repair, so the deadlock surfaces only
    add_sample_interval and drops the versioning clause from the message."""
    session = _bootstrap_snapshot_project(tmp_path, versioning=False, granularity="hour")

    with pytest.raises(ObservePlanningError) as captured:
        session.observe(ms.ref.metric("inventory.end_inventory"))

    context = captured.value._context
    assert context["code"] == "snapshot-fold-deadlock"
    assert [repair["action"] for repair in context["repair"]] == ["add_sample_interval"]
    assert context["repair"][0]["target"] == "inventory.snapshots.snapshot_date"
    assert context["repair"][0]["arg"] == "sample_interval"
    assert "either one" not in captured.value.message
    assert "only supports grain='day'" in captured.value.message


def test_snapshot_fold_deadlock_sub_day_non_selection_fold(tmp_path) -> None:
    """A sub-day non-selection fold deadlock surfaces only add_sample_interval; the
    message attributes the missing versioning path to the fold kind (versioning
    only legalizes first/last), not to the grain."""
    session = _bootstrap_snapshot_project(tmp_path, versioning=False, granularity="hour")

    with pytest.raises(ObservePlanningError) as captured:
        session.observe(ms.ref.metric("inventory.average_inventory"))

    context = captured.value._context
    assert context["code"] == "snapshot-fold-deadlock"
    assert [repair["action"] for repair in context["repair"]] == ["add_sample_interval"]
    assert "legalizes first/last folds" in captured.value.message
    assert "no versioning path" not in captured.value.message
