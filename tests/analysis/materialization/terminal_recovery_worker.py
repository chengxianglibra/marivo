"""Local subprocess entrypoint for terminal publication recovery checks."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from marivo.analysis.core.frame_data import CompletedFrameResult
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization import terminal_publication
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.session.core import Session


def method_result(*, empty: bool = False) -> CompletedFrameResult:
    """Small complete downstream fixture shared by publication and worker checks."""
    from marivo.analysis.core.artifact_data import DataColumn, DataSchema, ScalarDataType
    from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
    from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
    from marivo.analysis.core.frame_method_facts import (
        FrameAdditiveMethod,
        FrameMethodFacts,
        FrameMethodMetric,
        FrameMethodParent,
        FrameMethodSupport,
        FrameMethodValue,
    )

    roles = ("left", "right", "absolute", "contribution", "contribution_share")
    values = tuple(
        DataColumn(
            role,
            role + ".q",
            ScalarDataType("float64" if role == "contribution_share" else "int64"),
            False,
            "1" if role == "contribution_share" else "CNY",
        )
        for role in roles
    )
    presence = tuple(
        DataColumn(role, role, ScalarDataType("boolean"), False)
        for role in ("left_present", "right_present")
    )
    schema = DataSchema(
        (),
        values,
        presence,
        value_paths=tuple((role, "q") for role in roles),
        presence_paths=(("left_present",), ("right_present",)),
    )
    support = FrameMethodSupport("root.0", "rows", 1, 1, 1, 1)
    method = FrameAdditiveMethod(
        "additive",
        FrameMethodValue(values[0], 160),
        FrameMethodValue(values[1], 100),
        FrameMethodValue(values[2], 60),
        (support,),
    )
    facts = FrameMethodFacts(
        kind="attribute",
        origin_result_id="result_worker_method",
        output_schema=schema,
        coordinates=(),
        parent_axis_count=0,
        parents=()
        if empty
        else (FrameMethodParent((), (FrameMethodMetric("q", method, "defined"),)),),
    )
    rows = pd.DataFrame(
        {
            column.slot_id: pd.Series(
                [] if empty else [value],
                dtype="bool[pyarrow]"
                if type(value) is bool
                else "double[pyarrow]"
                if type(value) is float
                else "int64[pyarrow]",
            )
            for column, value in zip(
                schema.columns, (160, 100, 60, 60, 1.0, True, True), strict=True
            )
        }
    )
    metadata = FrameResultMetadata(
        "result_worker_method",
        FrameAudit(
            (), EvaluationRecord(datetime(2020, 1, 1, tzinfo=timezone.utc), (), ("attribute",))
        ),
    )
    return CompletedFrameResult(CompletedFrameData(schema, rows), metadata, (facts,))


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
        saved = AnalysisFrame._from_memory(runtime.local, method_result()).persist()
        print(saved.artifact.id)
    except SessionBusyError:
        sys.exit(38)


if __name__ == "__main__":
    main()
