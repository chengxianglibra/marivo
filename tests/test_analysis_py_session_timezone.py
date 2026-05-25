from zoneinfo import ZoneInfo

import pytest

import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import TimezoneInvalidError
from marivo.analysis_py.session.persistence import read_session_meta, write_session_meta


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def test_create_timezone_round_trips_through_meta():
    s = session_attach.create(name="demo", tz="Asia/Shanghai", default_calendar="cn_holidays")
    assert s.tz == ZoneInfo("Asia/Shanghai")
    assert s.default_calendar == "cn_holidays"
    meta = read_session_meta(s.layout)
    assert meta["tz"] == "Asia/Shanghai"
    assert meta["default_calendar"] == "cn_holidays"
    assert meta["known_calendars"] == []


def test_create_invalid_timezone_raises_structured_error():
    with pytest.raises(TimezoneInvalidError) as exc_info:
        session_attach.create(name="demo", tz="Mars/Olympus")
    assert exc_info.value.details["kind"] == "TimezoneNotFound"


def test_attach_legacy_meta_defaults_timezone_and_writes_back():
    s = session_attach.create(name="demo")
    meta = read_session_meta(s.layout)
    meta.pop("tz", None)
    meta.pop("default_calendar", None)
    meta.pop("known_calendars", None)
    write_session_meta(s.layout, meta)
    session_attach._reset_process_state()

    attached = session_attach.attach(name="demo")

    assert attached.tz == ZoneInfo("UTC")
    assert attached.default_calendar is None
    assert attached.known_calendars == set()
    upgraded = read_session_meta(attached.layout)
    assert upgraded["tz"] == "UTC"
    assert upgraded["default_calendar"] is None
    assert upgraded["known_calendars"] == []


def test_attach_explicit_timezone_overrides_meta_and_writes_back():
    s = session_attach.create(name="demo", tz="UTC")
    session_attach._reset_process_state()

    attached = session_attach.attach(
        name="demo", tz="Asia/Shanghai", default_calendar="cn_holidays"
    )

    assert attached.tz == ZoneInfo("Asia/Shanghai")
    assert attached.default_calendar == "cn_holidays"
    meta = read_session_meta(attached.layout)
    assert meta["tz"] == "Asia/Shanghai"
    assert meta["default_calendar"] == "cn_holidays"


def test_active_or_create_existing_active_applies_timezone_overrides():
    s = session_attach.create(name="demo", tz="UTC")

    attached = session_attach.active_or_create(
        name_hint="ignored",
        tz="Asia/Shanghai",
        default_calendar="cn_holidays",
    )

    assert attached.id == s.id
    assert attached.tz == ZoneInfo("Asia/Shanghai")
    assert attached.default_calendar == "cn_holidays"
    meta = read_session_meta(attached.layout)
    assert meta["tz"] == "Asia/Shanghai"
    assert meta["default_calendar"] == "cn_holidays"
