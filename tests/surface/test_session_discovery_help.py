"""Public Session discovery Help discloses absence without changing state."""

from pathlib import Path

import pytest

import marivo
from marivo._help import help as show_help


@pytest.mark.parametrize(
    ("target", "absence"),
    [
        ("analysis.session.current", "return None when the current-generation Store is absent"),
        ("analysis.session.recent", "return an empty page when its Store is absent"),
    ],
)
def test_session_discovery_help_describes_absence_without_creating_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    target: str,
    absence: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    old = tmp_path / ".marivo/analysis/generations/v5/session_store.db"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"preserve-old-store")

    assert marivo.help is show_help
    show_help(target)
    text = capsys.readouterr().out
    assert absence in text
    assert "No creation" in text
    assert old.read_bytes() == b"preserve-old-store"
    assert not (old.parent.parent / "v10").exists()


def test_observe_help_distinguishes_field_axes_from_session_identity(capsys):
    show_help("analysis.session.observe")
    text = capsys.readouterr().out
    assert "by=(region,)" in text
    assert "CalendarAxis" in text
    assert "None adds no time filter" in text
    assert "Choose name or id" not in text
    show_help("analysis.session.resume")
    assert "Choose name or id" in capsys.readouterr().out


def test_time_scope_help_routes_to_calendar_capable_producer(tmp_path, monkeypatch, capsys):
    import marivo.analysis as mv

    show_help("analysis.TimeScope")
    text = capsys.readouterr().out
    assert "session.time_scope" in text
    assert "legacy civil scopes" in text
    show_help("analysis.session.time_scope")
    assert "start=" in capsys.readouterr().out
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("calendar-help", report_timezone="UTC")
    scope = session.time_scope(start="2026-01-01", end="2026-02-01")
    assert len(scope.by(mv.grain("day")).grid.cells) == 31
    with pytest.raises(ValueError, match="frozen timezone"):
        mv.time_scope(start="2026-01-01", end="2026-02-01").by(mv.grain("day"))
    assert session.runs().items == ()
