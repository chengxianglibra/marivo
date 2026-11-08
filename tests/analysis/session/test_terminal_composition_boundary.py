"""Committed Frames retain data operations and expose audit without a wrapper."""

from pathlib import Path

import pytest

import marivo.analysis as mv


def test_new_and_reopened_frames_continue_composition_without_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("terminal-boundary")
    query = session.values(by=(), columns={"value": None}, rows=(((), {"value": 7}),))
    saved = query.persist()
    reopened = session.persisted_frame(saved.artifact_id)
    for result in (saved, reopened):
        compared = query.compare({"reference": mv.baseline(result)}).compute()
        assert compared.to_pandas()["reference.absolute.value"].tolist() == [0]
        aligned = query.align(result).compute()
        assert aligned.to_pandas()["right.value"].tolist() == [7]
    assert reopened.to_pandas().equals(saved.to_pandas())

    assert reopened.select({"double": reopened["value"] * 2}).compute().to_pandas()[
        "double"
    ].tolist() == [14]
