"""Native C13 replay against an independent occurrence and interval oracle."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.history_r76_oracle import assert_view, views
from tests.lifecycle_r75_fixtures import END, START, build_lifecycle_public
from tests.lifecycle_r75_oracle import expected_histories


@pytest.mark.runtime
@pytest.mark.parametrize("coverage", ["complete", "prefix", "unknown"])
def test_native_sqlite_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coverage: str
) -> None:
    monkeypatch.chdir(tmp_path)
    session, population, window, claims, facts = build_lifecycle_public(
        tmp_path, backend_name="sqlite"
    )
    known = (
        END
        if coverage == "complete"
        else START + timedelta(seconds=12)
        if coverage == "prefix"
        else None
    )
    supplied = () if known is None else (replace(claims[0], complete_through=known),)
    logical = session.lifecycle.replay(
        ms.ref.state_model("commerce.model"),
        population=population,
        window=window,
        seed=mv.from_inception(),
        completeness=supplied,
    )
    history = logical.execute()
    assert history._dataset is not None
    records = [
        json.loads(row["history__record"])
        for row in history._dataset.verified().parts[0].table.to_pylist()
    ]
    assert records == expected_histories(facts, known=known)
    expected = views(facts, known=known)
    for receiver in (logical, history):
        operations = {
            "in_state": receiver.read(
                mv.in_state(
                    ms.model_state(model=ms.ref.state_model("commerce.model"), name="done"), at=END
                )
            ),
            "distribution": receiver.distribution(at=(START, END)),
            "transitions": receiver.transitions(),
            "violations": receiver.violations(),
            "intervals": receiver.intervals(),
            "dwell": receiver.dwell(),
        }
        for name, operation in operations.items():
            assert_view(operation.execute(), name, expected)
        for relation in (
            operations["distribution"].known_state_count,
            operations["transitions"].count,
            operations["violations"].kind,
            operations["intervals"].observed_duration,
            operations["dwell"].mean_duration,
        ):
            relation.execute()
        if receiver is logical:

            def forbidden(*args: object, **kwargs: object) -> None:
                raise AssertionError("Fixed History consumer opened its source")

            monkeypatch.setattr(SourceSession, "batches", forbidden)
    script = """
import json, os, sys
from datetime import datetime
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from marivo.analysis.materialization import history_execution
from tests.history_r76_oracle import assert_view, views
from tests.lifecycle_r75_fixtures import START, END
from tests.lifecycle_r75_oracle import expected_histories
def forbidden(*args, **kwargs):
    raise AssertionError('Cold History opened a source or replayed events')
SourceSession.batches = forbidden
SemanticProject.load = forbidden
history_execution.replay = forbidden
ibis.duckdb.connect = forbidden
ibis.sqlite.connect = forbidden
os.chdir(sys.argv[1])
session = mv.session.resume(sys.argv[2], by='id')
history = session.artifact(sys.argv[3])
facts, known = json.loads(sys.argv[4])
known = None if known is None else datetime.fromisoformat(known)
records = [json.loads(row['history__record']) for row in history._dataset.verified().parts[0].table.to_pylist()]
assert records == expected_histories(facts, known=known)
expected = views(facts, known=known)
operations = {
    'in_state': history.read(mv.in_state(ms.model_state(model=ms.ref.state_model('commerce.model'),name='done'),at=END)),
    'distribution': history.distribution(at=(START,END)),
    'transitions': history.transitions(),
    'violations': history.violations(),
    'intervals': history.intervals(),
    'dwell': history.dwell(),
}
for name, operation in operations.items():
    assert_view(operation.execute(),name,expected)
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path),
            session.id,
            history.state.artifact_ref.ref,
            json.dumps([facts, None if known is None else known.isoformat()]),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
