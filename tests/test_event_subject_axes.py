"""Governed subject-axis planning and materialization."""

from __future__ import annotations

from typing import Any

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import InvalidSubjectAxisError
from marivo.analysis.intents._event_subject_axes import (
    materialize_subject_axes,
    resolve_subject_axes,
)


def _bootstrap_axis_project(tmp_path: Any) -> None:
    datasource_dir = tmp_path / "models" / "datasources"
    semantic_dir = tmp_path / "models" / "semantic" / "commerce"
    datasource_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "event-axes"\n')
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\n"
        "ms.domain(name='commerce', owner='Analytics', default=True)\n"
    )
    (semantic_dir / "model.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "users = ms.entity(\n"
        "    name='users', datasource=warehouse, source=md.table('users'),\n"
        "    primary_key=['user_id'],\n"
        "    ai_context=ms.ai_context(business_definition='One row per user.'),\n"
        ")\n"
        "profiles = ms.entity(\n"
        "    name='profiles', datasource=warehouse, source=md.table('profiles'),\n"
        "    primary_key=['user_id'],\n"
        "    ai_context=ms.ai_context(business_definition='One row per user profile.'),\n"
        ")\n"
        "orders = ms.entity(\n"
        "    name='orders', datasource=warehouse, source=md.table('orders'),\n"
        "    primary_key=['order_id'],\n"
        "    ai_context=ms.ai_context(business_definition='One row per order.'),\n"
        ")\n"
        "profile_daily = ms.entity(\n"
        "    name='profile_daily', datasource=warehouse, source=md.table('profile_daily'),\n"
        "    primary_key=['user_id', 'snapshot_date'],\n"
        "    versioning=ms.snapshot(\n"
        "        partition_field=ms.ref.dimension('commerce.profile_daily.snapshot_date'),\n"
        "        grain='day', timezone='UTC', format='%Y%m%d',\n"
        "    ),\n"
        "    ai_context=ms.ai_context(business_definition='Daily user profile snapshots.'),\n"
        ")\n"
        "profile_history = ms.entity(\n"
        "    name='profile_history', datasource=warehouse, source=md.table('profile_history'),\n"
        "    primary_key=['user_id', 'valid_from'],\n"
        "    versioning=ms.validity(\n"
        "        valid_from=ms.ref.dimension('commerce.profile_history.valid_from'),\n"
        "        valid_to=ms.ref.dimension('commerce.profile_history.valid_to'),\n"
        "        interval='closed_open', open_end=(None,), timezone='UTC',\n"
        "    ),\n"
        "    ai_context=ms.ai_context(business_definition='User profile validity history.'),\n"
        ")\n"
        "user_id = ms.dimension_column(name='user_id', entity=users, column='user_id')\n"
        "created_at = ms.time_dimension_column(\n"
        "    name='created_at', entity=users, column='created_at',\n"
        "    granularity='second', is_default=True,\n"
        ")\n"
        "profile_user_id = ms.dimension_column(\n"
        "    name='user_id', entity=profiles, column='user_id'\n"
        ")\n"
        "channel = ms.dimension_column(\n"
        "    name='channel', entity=profiles, column='channel'\n"
        ")\n"
        "order_id = ms.dimension_column(name='order_id', entity=orders, column='order_id')\n"
        "order_user_id = ms.dimension_column(\n"
        "    name='user_id', entity=orders, column='user_id'\n"
        ")\n"
        "order_type = ms.dimension_column(\n"
        "    name='order_type', entity=orders, column='order_type'\n"
        ")\n"
        "daily_user_id = ms.dimension_column(\n"
        "    name='user_id', entity=profile_daily, column='user_id'\n"
        ")\n"
        "snapshot_date = ms.dimension_column(\n"
        "    name='snapshot_date', entity=profile_daily, column='snapshot_date'\n"
        ")\n"
        "daily_tier = ms.dimension_column(\n"
        "    name='daily_tier', entity=profile_daily, column='tier'\n"
        ")\n"
        "history_user_id = ms.dimension_column(\n"
        "    name='user_id', entity=profile_history, column='user_id'\n"
        ")\n"
        "valid_from = ms.dimension_column(\n"
        "    name='valid_from', entity=profile_history, column='valid_from'\n"
        ")\n"
        "valid_to = ms.dimension_column(\n"
        "    name='valid_to', entity=profile_history, column='valid_to'\n"
        ")\n"
        "history_tier = ms.dimension_column(\n"
        "    name='history_tier', entity=profile_history, column='tier'\n"
        ")\n"
        "user_to_profile = ms.relationship(\n"
        "    name='user_to_profile', from_entity=users, to_entity=profiles,\n"
        "    keys=[ms.join_on(user_id, profile_user_id)],\n"
        ")\n"
        "user_to_orders = ms.relationship(\n"
        "    name='user_to_orders', from_entity=users, to_entity=orders,\n"
        "    keys=[ms.join_on(user_id, order_user_id)],\n"
        ")\n"
        "user_to_profile_daily = ms.relationship(\n"
        "    name='user_to_profile_daily', from_entity=users, to_entity=profile_daily,\n"
        "    keys=[ms.join_on(user_id, daily_user_id)],\n"
        ")\n"
        "user_to_profile_history = ms.relationship(\n"
        "    name='user_to_profile_history', from_entity=users, to_entity=profile_history,\n"
        "    keys=[ms.join_on(user_id, history_user_id)],\n"
        ")\n"
    )


def _axis_session(tmp_path: Any, monkeypatch: Any) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    _bootstrap_axis_project(tmp_path)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql("CREATE TABLE users (user_id VARCHAR, created_at TIMESTAMP)")
    backend.raw_sql(
        "INSERT INTO users VALUES "
        "('u1', TIMESTAMP '2026-07-01 00:00:00'), "
        "('u2', TIMESTAMP '2026-07-01 01:00:00')"
    )
    backend.raw_sql("CREATE TABLE profiles (user_id VARCHAR, channel VARCHAR)")
    backend.raw_sql("INSERT INTO profiles VALUES ('u1', 'paid'), ('u2', NULL)")
    backend.raw_sql("CREATE TABLE orders (order_id VARCHAR, user_id VARCHAR, order_type VARCHAR)")
    backend.raw_sql("INSERT INTO orders VALUES ('o1', 'u1', 'new'), ('o2', 'u1', 'repeat')")
    backend.raw_sql(
        "CREATE TABLE profile_daily (user_id VARCHAR, snapshot_date VARCHAR, tier VARCHAR)"
    )
    backend.raw_sql(
        "INSERT INTO profile_daily VALUES "
        "('u1', '20260630', 'u1_old'), ('u1', '20260701', 'u1_current'), "
        "('u2', '20260630', 'u2_current'), ('u2', '20260702', 'u2_future')"
    )
    backend.raw_sql(
        "CREATE TABLE profile_history ("
        "user_id VARCHAR, valid_from TIMESTAMP, valid_to TIMESTAMP, tier VARCHAR)"
    )
    backend.raw_sql(
        "INSERT INTO profile_history VALUES "
        "('u1', TIMESTAMP '2026-06-01 00:00:00', "
        " TIMESTAMP '2026-07-01 00:00:00', 'u1_old'), "
        "('u1', TIMESTAMP '2026-07-01 00:00:00', NULL, 'u1_current'), "
        "('u2', TIMESTAMP '2026-06-01 00:00:00', NULL, 'u2_current')"
    )
    return session_attach.get_or_create(
        name="event_axis_probe",
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )


def _journey_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "journey_id": ["j1", "j2"],
            "completion_status": ["complete", "complete"],
            "subject_identity": [("u1",), ("u2",)],
            "step_key": ["cart", "cart"],
            "event_identity": [("c1",), ("c2",)],
            "occurred_at": [
                pd.Timestamp("2026-07-01T00:00:00Z"),
                pd.Timestamp("2026-07-01T01:00:00Z"),
            ],
            "elapsed_from_start": [pd.Timedelta(0), pd.Timedelta(0)],
            "elapsed_from_previous": [None, None],
        }
    )


def test_subject_axis_plans_exact_entry_and_ref_identically(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _axis_session(tmp_path, monkeypatch)
    channel = session.catalog.require(ms.ref.dimension("commerce.profiles.channel"))
    assert isinstance(channel, ms.DimensionEntry)

    from_entry = resolve_subject_axes(
        session,
        subject_entity=ms.ref.entity("commerce.users"),
        axes=[channel],
    )
    from_ref = resolve_subject_axes(
        session,
        subject_entity=ms.ref.entity("commerce.users"),
        axes=[channel.ref],
    )

    assert tuple(item.binding for item in from_entry) == tuple(item.binding for item in from_ref)
    binding = from_entry[0].binding
    assert binding.output_column == "channel"
    assert tuple(item.path for item in binding.relationship_path) == ("commerce.user_to_profile",)
    assert binding.anchor == "cohort_entry"
    assert binding.versioning_resolution == "ordinary"


def test_subject_axis_materialization_keeps_explicit_null_value(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _axis_session(tmp_path, monkeypatch)
    axes = resolve_subject_axes(
        session,
        subject_entity=ms.ref.entity("commerce.users"),
        axes=[ms.ref.dimension("commerce.profiles.channel")],
    )

    materialized = materialize_subject_axes(
        session,
        journey_rows=_journey_rows(),
        first_step_key="cart",
        subject_entity=ms.ref.entity("commerce.users"),
        subject_identity=("commerce.users.user_id",),
        axes=axes,
    )

    values = materialized.values.set_index("subject_identity")
    assert values.loc[[("u1",)], "channel"].iloc[0] == "paid"
    assert pd.isna(values.loc[[("u2",)], "channel"].iloc[0])
    assert len(materialized.query_refs) == 1
    assert materialized.lineage[0]["anchor"] == "cohort_entry"


def test_subject_axis_rejects_time_duplicate_and_to_many_paths(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _axis_session(tmp_path, monkeypatch)
    with pytest.raises(InvalidSubjectAxisError):
        resolve_subject_axes(
            session,
            subject_entity=ms.ref.entity("commerce.users"),
            axes=[ms.ref.time_dimension("commerce.users.created_at")],
        )
    with pytest.raises(InvalidSubjectAxisError, match="repeat"):
        resolve_subject_axes(
            session,
            subject_entity=ms.ref.entity("commerce.users"),
            axes=[
                ms.ref.dimension("commerce.profiles.channel"),
                ms.ref.dimension("commerce.profiles.channel"),
            ],
        )
    with pytest.raises(InvalidSubjectAxisError, match="not to-one"):
        resolve_subject_axes(
            session,
            subject_entity=ms.ref.entity("commerce.users"),
            axes=[ms.ref.dimension("commerce.orders.order_type")],
        )


def test_subject_axis_rejects_output_colliding_with_operator_reserved_columns(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _axis_session(tmp_path, monkeypatch)
    # ``channel`` collides with the operator's row contract when it is declared
    # reserved (e.g. time-to-event reserves ``duration``/``completion_status``).
    with pytest.raises(InvalidSubjectAxisError, match="collides"):
        resolve_subject_axes(
            session,
            subject_entity=ms.ref.entity("commerce.users"),
            axes=[ms.ref.dimension("commerce.profiles.channel")],
            _reserved_columns=frozenset({"channel"}),
            operator="events.time_to_event",
        )
    with pytest.raises(InvalidSubjectAxisError, match="collides"):
        resolve_subject_axes(
            session,
            subject_entity=ms.ref.entity("commerce.users"),
            axes=[ms.ref.dimension("commerce.profiles.channel")],
            _reserved_columns=frozenset({"channel"}),
            operator="events.funnel",
        )


def test_subject_axis_snapshot_and_validity_use_cohort_entry_anchor(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _axis_session(tmp_path, monkeypatch)
    axes = resolve_subject_axes(
        session,
        subject_entity=ms.ref.entity("commerce.users"),
        axes=[
            ms.ref.dimension("commerce.profile_daily.daily_tier"),
            ms.ref.dimension("commerce.profile_history.history_tier"),
        ],
    )

    materialized = materialize_subject_axes(
        session,
        journey_rows=_journey_rows(),
        first_step_key="cart",
        subject_entity=ms.ref.entity("commerce.users"),
        subject_identity=("commerce.users.user_id",),
        axes=axes,
    )

    values = materialized.values.set_index("subject_identity")
    assert values.loc[[("u1",)], "daily_tier"].iloc[0] == "u1_current"
    assert values.loc[[("u2",)], "daily_tier"].iloc[0] == "u2_current"
    assert values.loc[[("u1",)], "history_tier"].iloc[0] == "u1_current"
    assert values.loc[[("u2",)], "history_tier"].iloc[0] == "u2_current"
    assert [binding.versioning_resolution for binding in materialized.bindings] == [
        "snapshot",
        "validity",
    ]
    assert len(materialized.query_refs) == 2


def test_subject_axis_rejects_missing_validity_value(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _axis_session(tmp_path, monkeypatch)
    backend = session._connection_runtime.session_backend("warehouse")
    backend.raw_sql("DELETE FROM profile_history WHERE user_id = 'u2'")
    axes = resolve_subject_axes(
        session,
        subject_entity=ms.ref.entity("commerce.users"),
        axes=[ms.ref.dimension("commerce.profile_history.history_tier")],
    )

    with pytest.raises(InvalidSubjectAxisError, match="no point-in-time row"):
        materialize_subject_axes(
            session,
            journey_rows=_journey_rows(),
            first_step_key="cart",
            subject_entity=ms.ref.entity("commerce.users"),
            subject_identity=("commerce.users.user_id",),
            axes=axes,
        )
