"""Session timezone contract: report timezone persistence and reopen conflict semantics."""

from dataclasses import asdict
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis.session as session_attach


def _read_session_meta(session: session_attach.Session) -> dict[str, object]:
    record = session._record()
    return asdict(record)


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield


def test_create_persists_system_report_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    s = session_attach.get_or_create(name="demo")

    assert s.report_tz == ZoneInfo("Asia/Shanghai")
    assert s.report_tz_name == "Asia/Shanghai"
    assert not hasattr(s, "default_calendar")
    meta = _read_session_meta(s)
    assert meta["report_timezone_name"] == "Asia/Shanghai"
    assert meta["report_timezone_resolution"] == "iana"
    assert "tz" not in meta
    assert "previous_tz" not in meta
    assert "default_calendar" not in meta


def test_create_accepts_explicit_report_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    s = session_attach.get_or_create(name="demo", report_timezone="UTC")

    assert s.report_tz == ZoneInfo("UTC")
    assert s.report_tz_name == "UTC"
    meta = _read_session_meta(s)
    assert meta["report_timezone_name"] == "UTC"
    assert meta["report_timezone_resolution"] == "iana"


def test_reopen_without_report_timezone_uses_persisted_value(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    created = session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")
    monkeypatch.setenv("TZ", "UTC")

    attached = session_attach.get_or_create(name="demo")

    assert created.id == attached.id
    assert attached.report_tz == ZoneInfo("Asia/Shanghai")
    assert _read_session_meta(attached)["report_timezone_name"] == "Asia/Shanghai"


def test_reopen_matching_report_timezone_is_idempotent(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    created = session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")

    attached = session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")

    assert attached.id == created.id
    assert attached.report_tz == ZoneInfo("Asia/Shanghai")


def test_reopen_conflicting_report_timezone_fails_closed(monkeypatch):
    from marivo.analysis.errors import SessionTimezoneConflict

    monkeypatch.setenv("TZ", "UTC")
    session_attach.get_or_create(name="demo", report_timezone="Asia/Shanghai")

    with pytest.raises(SessionTimezoneConflict) as exc_info:
        session_attach.get_or_create(name="demo", report_timezone="UTC")

    assert exc_info.value._context["persisted_report_tz"] == "Asia/Shanghai"
    assert exc_info.value._context["requested_report_tz"] == "UTC"
    assert "new named Session" in str(exc_info.value)


def test_reopen_conflicting_timezone_does_not_update_question(monkeypatch):
    from marivo.analysis.errors import SessionTimezoneConflict

    monkeypatch.setenv("TZ", "UTC")
    created = session_attach.get_or_create(
        name="demo",
        question="original",
        report_timezone="Asia/Shanghai",
    )
    meta_before = _read_session_meta(created)

    with pytest.raises(SessionTimezoneConflict):
        session_attach.get_or_create(
            name="demo",
            question="replacement",
            report_timezone="UTC",
        )

    persisted = session_attach.get_or_create(name="demo")
    assert persisted.question == "original"
    assert _read_session_meta(persisted)["question"] == meta_before["question"]


def test_create_does_not_initialize_legacy_calendar_directory(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    s = session_attach.get_or_create(name="demo")

    assert not (s.project_root / ".marivo" / "calendar").exists()


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


def test_fixed_offset_is_restored_without_system_timezone_changes(tmp_path, monkeypatch):
    from datetime import timedelta, timezone

    import marivo.analysis.timezone as timezone_owner
    from marivo.analysis.timezone import ResolvedTimezone

    fixed = timezone(timedelta(hours=5, minutes=30))
    monkeypatch.setattr(
        timezone_owner,
        "resolve_system_timezone",
        lambda: ResolvedTimezone("UTC+05:30", fixed, "fixed_offset"),
    )
    created = session_attach.get_or_create("fixed")
    monkeypatch.setenv("TZ", "America/New_York")
    recovered = session_attach.resume(created.id, by="id")
    assert recovered.report_tz == fixed
    assert recovered.report_tz_name == "UTC+05:30"
    assert recovered.report_tz_resolution == "fixed_offset"


def test_explicit_fixed_offset_is_normalized_and_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    created = session_attach.get_or_create(name="offset", report_timezone="+05:45")
    meta = _read_session_meta(created)
    assert meta["report_timezone_name"] == "UTC+05:45"
    assert meta["report_timezone_resolution"] == "fixed_offset"
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    reopened = session_attach.get_or_create(name="offset", report_timezone="UTC+05:45")
    assert _read_session_meta(reopened)["report_timezone_name"] == "UTC+05:45"
