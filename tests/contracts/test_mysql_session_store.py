from __future__ import annotations

import pytest

pytestmark = pytest.mark.mysql

# Guard: skip entire module when testcontainers or PyMySQL are not installed.
# These are optional extras (pip install marivo[test-mysql]) and will not be
# present in a default dev environment.
tc_mysql = pytest.importorskip("testcontainers.mysql")
pytest.importorskip("pymysql")

from testcontainers.mysql import MySqlContainer  # noqa: E402

from app.adapters.server.session_store import SqlSessionStore  # noqa: E402
from app.contracts.ids import SessionId  # noqa: E402
from app.contracts.session import SessionEvent  # noqa: E402
from app.storage.mysql_metadata import MySQLMetadataStore  # noqa: E402


@pytest.fixture(scope="module")
def mysql_metadata():
    with MySqlContainer("mysql:8.0") as mysql:
        host = mysql.get_container_host_ip()
        port = int(mysql.get_exposed_port(3306))
        store = MySQLMetadataStore(
            host=host,
            port=port,
            database="test",
            user="test",
            password="test",
        )
        store.initialize()
        yield store


def test_mysql_append_and_load(mysql_metadata, tmp_path):
    store = SqlSessionStore(mysql_metadata)
    sid = SessionId("mysql-sess-1")
    store.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "mysql test"},
            actor=None,
        ),
    )
    events = store.load_events(sid)
    assert len(events) == 1
    assert events[0].event_type == "session_created"
    assert events[0].session_id == sid
