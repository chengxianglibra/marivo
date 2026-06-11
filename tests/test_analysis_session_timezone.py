"""Session timezone contract: system resolver, no timezone= kwarg."""

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


def test_create_uses_system_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")

    assert s.tz == ZoneInfo("Asia/Shanghai")
    assert s.default_calendar == "cn_holidays"
    meta = _read_session_meta(s)
    assert meta["tz"] == "Asia/Shanghai"
    assert meta["tz_resolution"] == "iana"
    assert meta["tz_warning"] is None
    assert meta["default_calendar"] == "cn_holidays"


def test_session_helpers_reject_timezone_kwarg():
    with pytest.raises(TypeError):
        session_attach.get_or_create(name="demo", timezone="UTC")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        session_attach.get_or_create(name="fresh", timezone="UTC")  # type: ignore[call-arg]


def test_attach_legacy_meta_preserves_existing_audit_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    s = session_attach.get_or_create(name="demo")
    meta = _read_session_meta(s)
    meta["tz"] = "UTC"
    meta.pop("tz_resolution", None)
    meta.pop("tz_warning", None)
    _write_session_meta(s, meta)
    session_attach._reset_process_state()

    attached = session_attach.get_or_create(name="demo")

    assert attached.tz == ZoneInfo("Asia/Shanghai")
    upgraded = _read_session_meta(attached)
    assert upgraded["tz"] == "Asia/Shanghai"
    assert upgraded["previous_tz"] == "UTC"


def test_create_initializes_project_calendar_directory(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")

    assert (s.project_root / ".marivo" / "calendar").is_dir()


def test_create_rejects_legacy_tz_kwarg():
    with pytest.raises(TypeError):
        session_attach.get_or_create(name="demo", tz="UTC")  # type: ignore[call-arg]


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
