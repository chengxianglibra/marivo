"""Event Journey persistence transaction and hot-reuse regressions."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import ibis
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms


def _bootstrap_event_project(root: Path) -> None:
    datasource_dir = root / "models" / "datasources"
    semantic_dir = root / "models" / "semantic" / "commerce"
    datasource_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    (root / "marivo.toml").write_text('[project]\nname = "event-persistence"\n')
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\n"
        "ms.domain(name='commerce', owner='Analytics', default=True)\n"
    )
    (semantic_dir / "events.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "users = ms.entity(\n"
        "    name='users', datasource=warehouse, source=md.table('users'),\n"
        "    primary_key=['user_id'],\n"
        "    ai_context=ms.ai_context(business_definition='One row per user.'),\n"
        ")\n"
        "event_log = ms.entity(\n"
        "    name='event_log', datasource=warehouse, source=md.table('event_log'),\n"
        "    primary_key=['event_id'],\n"
        "    ai_context=ms.ai_context(business_definition='One row per event.'),\n"
        ")\n"
        "user_id = ms.dimension_column(name='user_id', entity=users, column='user_id')\n"
        "event_id = ms.dimension_column(name='event_id', entity=event_log, column='event_id')\n"
        "event_user_id = ms.dimension_column(\n"
        "    name='user_id', entity=event_log, column='user_id'\n"
        ")\n"
        "event_type = ms.dimension_column(\n"
        "    name='event_type', entity=event_log, column='event_type'\n"
        ")\n"
        "event_time = ms.time_dimension_column(\n"
        "    name='event_time', entity=event_log, column='event_time',\n"
        "    granularity='second', is_default=True,\n"
        ")\n"
        "event_to_user = ms.relationship(\n"
        "    name='event_to_user', from_entity=event_log, to_entity=users,\n"
        "    keys=[ms.join_on(event_user_id, user_id)],\n"
        ")\n\n"
        "@ms.event(\n"
        "    name='cart_created', identity=(event_id,), occurred_at=event_time,\n"
        "    participants=(ms.participant(\n"
        "        name='user', path=(event_to_user,), cardinality='one'\n"
        "    ),),\n"
        "    ai_context=ms.ai_context(business_definition='A cart was created.'),\n"
        ")\n"
        "def cart_created(rows):\n"
        "    return ms.bind(event_type, rows) == 'cart_created'\n\n"
        "@ms.event(\n"
        "    name='payment_succeeded', identity=(event_id,), occurred_at=event_time,\n"
        "    participants=(ms.participant(\n"
        "        name='buyer', path=(event_to_user,), cardinality='one'\n"
        "    ),),\n"
        "    ai_context=ms.ai_context(business_definition='A payment succeeded.'),\n"
        ")\n"
        "def payment_succeeded(rows):\n"
        "    return ms.bind(event_type, rows) == 'payment_succeeded'\n"
    )


def _event_match_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_name: str,
) -> tuple[mv.Session, dict[str, Any]]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    _bootstrap_event_project(tmp_path)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql("CREATE TABLE users (user_id VARCHAR)")
    backend.raw_sql("INSERT INTO users VALUES ('u1')")
    backend.raw_sql(
        "CREATE TABLE event_log ("
        "event_id VARCHAR, user_id VARCHAR, event_type VARCHAR, event_time TIMESTAMP)"
    )
    backend.raw_sql(
        "INSERT INTO event_log VALUES "
        "('e1', 'u1', 'cart_created', TIMESTAMP '2026-07-01 01:00:00'),"
        "('e2', 'u1', 'payment_succeeded', TIMESTAMP '2026-07-01 02:00:00')"
    )
    session = session_attach.get_or_create(
        name=session_name,
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    pattern = mv.sequence(
        mv.step(
            participant=ms.participant_role(event=cart, name="user"),
            key="cart",
        ),
        mv.step(
            participant=ms.participant_role(event=payment, name="buyer"),
            key="payment",
        ),
    )
    through = "2026-07-02T00:00:00Z"
    return session, {
        "pattern": pattern,
        "cohort_window": mv.TimeScope(
            start="2026-07-01T00:00:00Z",
            end=through,
        ),
        "completion_through": through,
        "matching": mv.first_per_subject(),
        "completeness": (
            mv.declared_complete_through(
                inputs=(cart, payment),
                through=through,
                rationale="The fixture is reconciled.",
            ),
        ),
    }


@pytest.mark.parametrize(
    "failure_target",
    ["register_frame_artifact", "persist_job_record"],
)
def test_event_match_rolls_back_artifact_evidence_and_job_on_late_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    session, kwargs = _event_match_case(
        tmp_path,
        monkeypatch,
        session_name="event_transaction",
    )
    event_module = importlib.import_module("marivo.analysis.intents.events")

    def fail_persistence(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced late persistence failure")

    monkeypatch.setattr(event_module, failure_target, fail_persistence)
    try:
        with pytest.raises(RuntimeError, match="forced late persistence failure"):
            session.events.match(**kwargs)

        assert session._store.list_artifacts(session.id) == []
        assert session._store.list_jobs(session.id) == []
        assert list(session._layout.frames_dir.glob("*")) == []
        assert list(session._layout.jobs_dir.glob("*.json")) == []
        evidence_store = session._evidence_store()
        assert evidence_store is not None
        evidence_count = (
            evidence_store.read()
            .execute(
                "SELECT COUNT(*) FROM artifacts WHERE session_id = ?",
                (session.id,),
            )
            .fetchone()[0]
        )
        assert evidence_count == 0
    finally:
        session.close()
        session_attach._reset_process_state()


def test_event_match_hot_reuse_restores_tuple_identity_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, kwargs = _event_match_case(
        tmp_path,
        monkeypatch,
        session_name="event_hot_reuse",
    )
    try:
        first = session.events.match(**kwargs)
        reused = session.events.match(**kwargs)

        assert reused.ref == first.ref
        assert reused.to_pandas()["subject_identity"].map(type).eq(tuple).all()
        assert (
            reused.to_pandas()
            .loc[reused.to_pandas()["event_identity"].notna(), "event_identity"]
            .map(type)
            .eq(tuple)
            .all()
        )
    finally:
        session.close()
        session_attach._reset_process_state()
