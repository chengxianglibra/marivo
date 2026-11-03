"""Creation freezes strict system or explicit timezone; recovery uses saved facts."""

import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

import marivo._timezones as timezone_names
import marivo.analysis.session as session_attach
from marivo.analysis.errors import ReportTimezoneUnresolved, SessionStateError, TimezoneInvalidError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.store import SessionStore
from tests.support.paths import PROJECT_ROOT


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_create_persists_system_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session = session_attach.get_or_create("system")
    assert session.timezone == "Asia/Shanghai"
    assert session.timezone_origin == "system"
    meta = asdict(session._record())
    assert meta["report_timezone_name"] == "Asia/Shanghai"
    assert meta["report_timezone_resolution"] == "iana"
    assert meta["report_timezone_origin"] == "system"
    assert "timezone=Asia/Shanghai (system)" in repr(session)
    assert "timezone: Asia/Shanghai (system)" in session.render()
    for retired in ("report_tz", "report_tz_name", "report_tz_resolution", "default_calendar"):
        assert not hasattr(session, retired)
    monkeypatch.setenv("TZ", "UTC")
    restored = session_attach.resume(session.id)
    assert restored.timezone == "Asia/Shanghai"
    assert restored.timezone_origin == "system"
    assert restored.instant("2026-01-01") == session.instant("2026-01-01", timezone="Asia/Shanghai")
    assert (
        restored.time_scope(start="2026-01-01", end="2026-02-01").boundary_timezone
        == "Asia/Shanghai"
    )
    summary = session_attach.inspect(session.id)
    assert summary.timezone == "Asia/Shanghai"
    assert summary.timezone_origin == "system"
    assert session_attach.recent().items[0].timezone_origin == "system"


def test_explicit_timezone_and_fixed_offset_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC+08:00")
    session = session_attach.get_or_create("explicit", timezone="Etc/UTC")
    assert session.timezone == "UTC"
    assert session.timezone_origin == "explicit"
    assert session_attach.get_or_create("explicit", timezone="+00:00").id == session.id
    offset = session_attach.get_or_create("offset", timezone="+08:00")
    assert offset.timezone == "UTC+08:00"
    assert offset.timezone_origin == "explicit"
    assert asdict(offset._record())["report_timezone_resolution"] == "fixed_offset"
    assert "timezone=UTC+08:00 (explicit)" in repr(offset)
    assert "timezone: UTC+08:00 (explicit)" in offset.render()
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    assert session_attach.resume(offset.id).timezone == "UTC+08:00"
    assert session_attach.get_or_create("offset", timezone="Etc/GMT-8").id == offset.id


def test_other_iana_links_conflict_with_actual_canonical_repair() -> None:
    session = session_attach.get_or_create("named-link", timezone="PRC")
    with pytest.raises(SessionStateError) as caught:
        session_attach.get_or_create("named-link", timezone="Asia/Shanghai")
    assert caught.value.expected == "saved timezone: 'PRC'"
    assert caught.value.received == "requested timezone: 'Asia/Shanghai'"
    assert caught.value.repair is not None
    assert caught.value.repair.snippet is not None
    assert "timezone='PRC'" in caught.value.repair.snippet
    assert caught.value.repair.candidates == (session.id,)


def test_invalid_explicit_timezone_never_falls_back() -> None:
    with pytest.raises(TimezoneInvalidError):
        session_attach.get_or_create("bad", timezone="Mars/Olympus")
    assert session_attach.recent().items == ()
    original = session_attach.get_or_create("existing", timezone="UTC")
    before = original._record()
    with pytest.raises(TimezoneInvalidError):
        session_attach.get_or_create("existing", timezone="Mars/Olympus")
    assert original._record() == before


@pytest.mark.parametrize("value", ("UTC+08:00", "UTC-8", "", "Mars/Olympus", "::Asia/Tokyo"))
def test_system_timezone_invalid_tz_requires_explicit_input(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", value)
    with pytest.raises(ReportTimezoneUnresolved) as caught:
        session_attach.get_or_create("invalid-system")
    error = caught.value
    assert error.source == "TZ"
    assert error.system_value == value
    assert error.repair is not None
    assert error.repair.snippet is not None
    assert 'timezone="..."' in error.repair.snippet
    assert session_attach.recent().items == ()


@pytest.mark.parametrize("value", ("Asia/Tokyo", ":Asia/Tokyo"))
def test_system_timezone_accepts_one_optional_colon(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", value)
    session = session_attach.get_or_create("tokyo")
    assert session.timezone == "Asia/Tokyo"
    assert session.timezone_origin == "system"


@pytest.mark.parametrize("state", ("missing", "regular", "zoneinfo", "outside", "broken"))
def test_system_timezone_uses_localtime_identity_without_file_content_guessing(
    state: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(timezone_names, "_SYSTEM_PLATFORM", "linux")
    localtime = tmp_path / "localtime"
    monkeypatch.setattr(timezone_names, "_LOCALTIME_PATH", localtime)
    if state == "regular":
        localtime.write_bytes(b"timezone file contents")
    elif state in ("zoneinfo", "outside", "broken"):
        target = tmp_path / ("zoneinfo/Asia/Tokyo" if state == "zoneinfo" else "other-zone")
        if state != "broken":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"timezone file contents")
        localtime.symlink_to(target)
    if state in ("missing", "zoneinfo"):
        session = session_attach.get_or_create("localtime")
        assert session.timezone == ("UTC" if state == "missing" else "Asia/Tokyo")
        assert session.timezone_origin == "system"
    else:
        with pytest.raises(ReportTimezoneUnresolved) as caught:
            session_attach.get_or_create("localtime")
        assert caught.value.source == "localtime"
        assert session_attach.recent().items == ()


def test_windows_without_tz_requires_explicit_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(timezone_names, "_SYSTEM_PLATFORM", "win32")
    with pytest.raises(ReportTimezoneUnresolved, match="Windows without TZ"):
        session_attach.get_or_create("windows")
    assert session_attach.get_or_create("explicit-windows", timezone="UTC").timezone == "UTC"


@pytest.mark.parametrize("host_timezone", ("UTC+08:00", "America/New_York", None))
def test_explicit_session_and_recovery_never_resolve_host_timezone(
    host_timezone: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.timezone as timezone_owner

    if host_timezone is None:
        monkeypatch.delenv("TZ", raising=False)
    else:
        monkeypatch.setenv("TZ", host_timezone)
    monkeypatch.setattr(
        timezone_owner,
        "resolve_system_timezone",
        lambda: pytest.fail("explicit creation or recovery resolved the host timezone"),
    )
    session = session_attach.get_or_create("fixed", timezone="+05:30")
    assert session.instant("2026-01-01").isoformat() == "2025-12-31T18:30:00+00:00"
    before = session._record()
    assert session_attach.resume(session.id)._record() == before
    assert session_attach.get_or_create("fixed")._record() == before
    assert session_attach.get_or_create("fixed", timezone="UTC+05:30")._record() == before
    assert before.report_timezone_origin == "explicit"


def test_session_record_rejects_missing_origin_instead_of_defaulting() -> None:
    session = session_attach.get_or_create("stored", timezone="UTC")
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT session_ref,name,question,report_timezone_name,report_timezone_resolution,"
            "created_at,updated_at,domains_payload FROM sessions"
        ).fetchone()
    with pytest.raises(IntegrityError, match="origin is missing"):
        SessionStore._session(row)


def test_cold_session_recovery_retains_system_origin_without_host_or_semantic_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    session = session_attach.get_or_create("cold-system")
    code = """
import sys
from unittest.mock import patch
import marivo.analysis.session as sessions
import marivo.analysis.timezone as timezones
import marivo.semantic.catalog as catalog

def forbidden(*args, **kwargs):
    raise AssertionError('recovery read host state or semantics')

with patch.object(timezones, 'resolve_system_timezone', forbidden), patch.object(catalog, 'load', forbidden):
    session = sessions.resume(sys.argv[1])
    assert session.timezone == 'Asia/Tokyo'
    assert session.timezone_origin == 'system'
    assert session.instant('2026-01-01').isoformat() == '2025-12-31T15:00:00+00:00'
    assert session.time_scope(start='2026-01-01', end='2026-01-02').boundary_timezone == 'Asia/Tokyo'
    assert sessions.get_or_create('cold-system').id == session.id
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, session.id],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), TZ="UTC+08:00"),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
