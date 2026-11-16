"""Cold public recovery and complete payload reads, with replay paths forbidden."""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import marivo
import marivo.analysis as mv
from marivo.analysis.materialization import terminal_publication
from marivo.datasource.adapters import SourceSession


def snapshot(frame: mv.AnalysisFrame) -> str:
    """Compare captured cells, physical dtypes, calendar details and method payloads."""
    table = frame.to_pandas(time_details=True)
    return json.dumps(
        {
            "table": json.loads(
                table.to_json(orient="split", index=False, date_format="iso", date_unit="ns")
            ),
            "dtypes": [str(dtype) for dtype in table.dtypes],
            "presence": frame.presence_names,
            "axes": [(axis.role, axis.key_names) for axis in frame.axes],
            "methods": [asdict(facts) for facts in frame.method_facts],
        },
        default=str,
        sort_keys=True,
    )


def _forbid(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Cold recovery must not bind a source, compute, or publish")


def main() -> None:
    assert Path(marivo.__file__).resolve().parents[1] == Path(__file__).resolve().parents[2], (
        "reader imported a different checkout"
    )
    root, session_id = sys.argv[1:]
    os.chdir(root)
    with (
        patch.object(mv.Session, "catalog", property(_forbid)),
        patch.object(SourceSession, "bind", _forbid),
        patch.object(SourceSession, "execute", _forbid),
        patch.object(mv.AnalysisFrame, "compute", _forbid),
        patch.object(terminal_publication, "publish_completed", _forbid),
    ):
        session = mv.session.resume(session_id)
        result = {
            item.artifact_id: snapshot(session.persisted_frame(item.artifact_id))
            for item in session.persisted_frames().items
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
