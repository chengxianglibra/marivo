"""Session timezone contract: report timezone persistence and reopen conflict semantics."""

import json
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis.session as session_attach


def _read_session_meta(session: object) -> dict:
    layout = session._layout  # type: ignore[attr-defined]
    return json.loads((layout.session_dir / "meta.json").read_text())


def _write_session_meta(session: object, meta: dict) -> None:
    layout = session._layout  # type: ignore[attr-defined]
    (layout.session_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_create_persists_system_report_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")

    assert s.report_tz == ZoneInfo("Asia/Shanghai")
    assert s.tz == ZoneInfo("Asia/Shanghai")
    assert s.report_tz_name == "Asia/Shanghai"
    assert s.default_calendar == "cn_holidays"
    meta = _read_session_meta(s)
    assert meta["report_tz"] == "Asia/Shanghai"
    assert meta["report_tz_resolution"] == "iana"
    assert meta["report_tz_warning"] is None
    assert "tz" not in meta
    assert "previous_tz" not in meta
    assert meta["default_calendar"] == "cn_holidays"


def test_create_accepts_explicit_report_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    s = session_attach.get_or_create(name="demo", report_timezone="UTC")

    assert s.report_tz == ZoneInfo("UTC")
    assert s.report_tz_name == "UTC"
    meta = _read_session_meta(s)
    assert meta["report_tz"] == "UTC"
    assert meta["report_tz_resolution"] == "iana"
    assert meta["report_tz_warning"] is None


def test_reopen_without_report_timezone_uses_persisted_value(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    created = session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")
    session_attach._reset_process_state()
    monkeypatch.setenv("TZ", "UTC")

    attached = session_attach.get_or_create(name="demo")

    assert created.id == attached.id
    assert attached.report_tz == ZoneInfo("Asia/Shanghai")
    assert _read_session_meta(attached)["report_tz"] == "Asia/Shanghai"


def test_reopen_matching_report_timezone_is_idempotent(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    created = session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")
    session_attach._reset_process_state()

    attached = session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")

    assert attached.id == created.id
    assert attached.report_tz == ZoneInfo("Asia/Shanghai")


def test_reopen_conflicting_report_timezone_fails_closed(monkeypatch):
    from marivo.analysis.errors import SessionTimezoneConflict

    monkeypatch.setenv("TZ", "UTC")
    session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")
    session_attach._reset_process_state()

    with pytest.raises(SessionTimezoneConflict) as exc_info:
        session_attach.get_or_create(name="demo", report_timezone="UTC")

    assert exc_info.value.details["persisted_report_tz"] == "Asia/Shanghai"
    assert exc_info.value.details["requested_report_tz"] == "UTC"
    assert "delete and recreate" in str(exc_info.value)


def test_create_initializes_project_calendar_directory(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")

    assert (s.project_root / ".marivo" / "calendar").is_dir()


def test_system_timezone_prefers_tz_environment(monkeypatch):
    from marivo.analysis.timezone import resolve_system_timezone

    monkeypatch.setenv("TZ", "Asia/Shanghai")

    resolved = resolve_system_timezone()

    assert str(resolved.tz) == "Asia/Shanghai"
    assert resolved.name == "Asia/Shanghai"
    assert resolved.resolution == "iana"
    assert resolved.warning is None


def test_system_timezone_invalid_tz_falls_back_to_local_offset(monkeypatch):
    from datetime import tzinfo

    from marivo.analysis.timezone import resolve_system_timezone

    monkeypatch.setenv("TZ", "Mars/Olympus")

    resolved = resolve_system_timezone()

    assert isinstance(resolved.tz, tzinfo)
    assert resolved.resolution == "fixed_offset"
    assert (
        resolved.warning
        == "system timezone could not be resolved as IANA; fixed offset fallback is in use"
    )
