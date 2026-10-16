"""Local subprocess entrypoint for terminal publication recovery checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from marivo._definition.expressions import MetricConstant
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.session.core import Session
from marivo.semantic.expressions import Metric


def main() -> None:
    root, name, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]

    def event(current: str) -> None:
        if current == point:
            os._exit(37)

    try:
        runtime = DatasetRuntime.create(root, name, report_timezone="UTC", event=event)
        session = Session._from_runtime(runtime)
        artifact = session.observe({"answer": Metric(MetricConstant(7), (), None)}).execute()
        print(artifact.id)
    except SessionBusyError:
        sys.exit(38)


if __name__ == "__main__":
    main()
