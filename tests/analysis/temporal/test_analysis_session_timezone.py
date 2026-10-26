"""Creation freezes system or explicit timezone; recovery restores saved facts."""

from dataclasses import asdict
from datetime import timedelta, timezone, tzinfo
from pathlib import Path

import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import TimezoneInvalidError


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_create_persists_system_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session = session_attach.get_or_create("system")
    assert session.timezone == "Asia/Shanghai"
    meta = asdict(session._record())
    assert meta["report_timezone_name"] == "Asia/Shanghai"
    assert meta["report_timezone_resolution"] == "iana"
    for retired in ("report_tz", "report_tz_name", "report_tz_resolution", "default_calendar"):
        assert not hasattr(session, retired)
    monkeypatch.setenv("TZ", "UTC")
    restored = session_attach.resume(session.id)
    assert restored.timezone == "Asia/Shanghai"
    assert restored.instant("2026-01-01") == session.instant("2026-01-01", timezone="Asia/Shanghai")
    assert (
        restored.time_scope(start="2026-01-01", end="2026-02-01").boundary_timezone
        == "Asia/Shanghai"
    )


def test_explicit_timezone_and_fixed_offset_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session = session_attach.get_or_create("explicit", timezone="UTC")
    assert session.timezone == "UTC"
    offset = session_attach.get_or_create("offset", timezone="+05:45")
    assert offset.timezone == "UTC+05:45"
    assert asdict(offset._record())["report_timezone_resolution"] == "fixed_offset"
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    assert session_attach.resume(offset.id).timezone == "UTC+05:45"


def test_invalid_explicit_timezone_never_falls_back() -> None:
    with pytest.raises(TimezoneInvalidError):
        session_attach.get_or_create("bad", timezone="Mars/Olympus")
    assert session_attach.recent().items == ()
    original = session_attach.get_or_create("existing", timezone="UTC")
    before = original._record()
    with pytest.raises(TimezoneInvalidError):
        session_attach.get_or_create("existing", timezone="Mars/Olympus")
    assert original._record() == before


def test_system_timezone_invalid_tz_falls_back_to_local_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.timezone import resolve_system_timezone

    monkeypatch.setenv("TZ", "Mars/Olympus")
    resolved = resolve_system_timezone()
    assert isinstance(resolved.tz, tzinfo)
    assert resolved.resolution == "fixed_offset"
    assert (
        resolved.warning
        == "system timezone could not be resolved as IANA; fixed offset fallback is in use"
    )


def test_fixed_offset_recovery_never_rechecks_system_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.analysis.timezone as timezone_owner
    from marivo.analysis.timezone import ResolvedTimezone

    fixed = timezone(timedelta(hours=5, minutes=30))
    monkeypatch.setattr(
        timezone_owner,
        "resolve_system_timezone",
        lambda: ResolvedTimezone("UTC+05:30", fixed, "fixed_offset"),
    )
    session = session_attach.get_or_create("fixed")
    monkeypatch.setattr(
        timezone_owner,
        "resolve_system_timezone",
        lambda: pytest.fail("recovery rechecked the system timezone"),
    )
    recovered = session_attach.resume(session.id)
    assert recovered.timezone == "UTC+05:30"
    assert recovered._record().report_timezone_resolution == "fixed_offset"
