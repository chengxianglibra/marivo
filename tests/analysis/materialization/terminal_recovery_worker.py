"""Local subprocess entrypoint for terminal publication recovery checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.session.core import Session
from tests.shared_fixtures import numeric_literal_node, numeric_program_query


def main() -> None:
    root, name, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]

    def event(current: str) -> None:
        if current == point:
            os._exit(37)

    try:
        runtime = DatasetRuntime.create(root, name, report_timezone="UTC", event=event)
        session = Session._from_runtime(runtime)
        artifact = numeric_program_query(session, {"answer": numeric_literal_node(7)}).execute()
        print(artifact.id)
    except SessionBusyError:
        sys.exit(38)


if __name__ == "__main__":
    main()
