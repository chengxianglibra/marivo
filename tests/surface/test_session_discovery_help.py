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
    assert not (old.parent.parent / "v9").exists()
