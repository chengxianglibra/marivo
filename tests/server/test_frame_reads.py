"""Check the independent stored read path before the HTTP boundary."""

import json
import subprocess
import sys
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

from marivo.server.frames import FrameReads
from marivo.server.wire import Budget, Limits


def test_saved_projection_after_producer_exit(tmp_path: Path) -> None:
    ids = json.loads(
        subprocess.check_output(
            [sys.executable, str(Path(__file__).with_name("producer.py")), str(tmp_path)]
        )
    )
    reads = FrameReads(tmp_path)
    session, artifact = ids["session_id"], ids["artifact_id"]

    def report(artifact_id: str) -> dict[str, Any]:
        budget = Budget(Limits(), "frame", monotonic() + 30, Event())
        body = reads.report(session, artifact_id, budget)
        assert len(body) == budget.used
        decoded: object = json.loads(body)
        assert isinstance(decoded, dict)
        return decoded

    detail = report(artifact)
    assert detail["description"] == "service fixture"
    assert detail["schema"]["value_time_semantics"] == {}
    assert detail["source_reads"] == []
    assert detail["periods"] == []
    assert detail["schema"]["values"]["amount"]["dtype"] == {
        "kind": "decimal",
        "precision": 25,
        "scale": 3,
    }
    data = detail["data"]
    assert data["coordinates"] == {
        "key": ["9007199254740993", "9007199254740995", "9007199254740997"]
    }
    assert data["rows"] == [
        [
            "9007199254740993",
            "9007199254740993.100",
            "2023-11-14T22:13:20.000000001+00:00",
            "1",
            1.5,
            True,
        ],
        [
            "9007199254740995",
            None,
            "2023-11-14T22:13:20.000000002+00:00",
            "2",
            {"float": "nan"},
            True,
        ],
        [
            "9007199254740997",
            None,
            "1677-09-21T00:12:43.145224192+00:00",
            "-3",
            {"float": "inf"},
            False,
        ],
    ]
    assert data["row_count"] == 3
    empty = report(ids["empty_id"])["data"]
    assert empty["rows"] == [] and empty["coordinates"] == {"key": []}
    assert empty["row_count"] == 0
