"""Local subprocess entrypoint for terminal publication recovery checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from marivo.analysis.materialization import terminal_publication
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.session.core import Session


def main() -> None:
    from marivo import __file__ as marivo_path

    assert Path(marivo_path).resolve().parents[1] == Path(__file__).resolve().parents[3], (
        "worker imported a different checkout"
    )
    root, session_id, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]

    def event(current: str) -> None:
        if current == point:
            os._exit(37)

    try:
        runtime = DatasetRuntime.open(root, session_id, event=event)
        session = Session._from_runtime(runtime)
        terminal_publication._event = event
        saved = session.values(
            by=(), columns={"answer": None}, rows=(((), {"answer": 7}),)
        ).persist()
        print(saved.artifact.id)
    except SessionBusyError:
        sys.exit(38)


if __name__ == "__main__":
    main()
