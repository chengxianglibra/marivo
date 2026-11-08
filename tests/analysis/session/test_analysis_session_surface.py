"""Lock the public analysis Session surface without publication bookkeeping."""

from __future__ import annotations

from pathlib import Path

import pytest

import marivo.analysis as mv


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create(name="surface_probe")


def test_dir_advertises_intents_and_hides_plumbing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch)
    names = set(dir(session))
    for advertised in (
        "observe",
        "members",
        "frame",
        "persisted_frame",
        "persisted_frames",
        "catalog",
    ):
        assert advertised in names, f"missing advertised member: {advertised}"
