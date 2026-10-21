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


def method_result(
    *, empty: bool = False, current: int = 160, baseline: int = 100, unit: str = "CNY"
) -> CompletedFrameResult:
    """Small complete downstream fixture shared by publication and worker checks."""
    from marivo.analysis.core.artifact_data import DataColumn, DataSchema, ScalarDataType
    from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
    from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
    from marivo.analysis.core.frame_method_facts import (
        FrameAdditiveMethod,
        FrameAttributeMetric,
        FrameMethodFacts,
        FrameMethodOutput,
        FrameMethodParent,
        FrameMethodSupport,
        FrameMethodTarget,
        FrameMethodValue,
    )

    roles = (
        "contribution",
        "total",
        "observed_total",
        "contribution_share",
        "detail.current",
        "detail.baseline",
        "detail.absolute",
    )
    values = tuple(
        DataColumn(
            role,
            role + ".q",
            ScalarDataType("float64" if role == "contribution_share" else "int64"),
            False,
            "1" if role == "contribution_share" else unit,
        )
        for role in roles
    )
    presence = tuple(
        DataColumn(role, role, ScalarDataType("boolean"), False)
        for role in ("detail.current.present.q", "detail.baseline.present.q")
    )
    schema = DataSchema(
        (),
        values,
        presence,
        value_paths=tuple((*role.split("."), "q") for role in roles),
        presence_paths=tuple(tuple(column.name.split(".")) for column in presence),
    )
    support = FrameMethodSupport("root.0", "rows", 1, 1, 1, 1)
    method = FrameAdditiveMethod(
        "additive",
        FrameMethodValue(values[4], current),
        FrameMethodValue(values[5], baseline),
        FrameMethodValue(values[6], current - baseline),
        (support,),
    )
    outputs = (
        FrameMethodOutput("contribution", ("contribution", "q")),
        FrameMethodOutput("total", ("total", "q")),
        FrameMethodOutput("observed_total", ("observed_total", "q")),
        FrameMethodOutput("share", ("contribution_share", "q")),
        FrameMethodOutput("current", ("detail", "current", "q")),
        FrameMethodOutput("baseline", ("detail", "baseline", "q")),
        FrameMethodOutput("absolute", ("detail", "absolute", "q")),
        FrameMethodOutput("current_present", ("detail", "current", "present", "q")),
        FrameMethodOutput("baseline_present", ("detail", "baseline", "present", "q")),
    )
    metric = FrameAttributeMetric(
        ("q",),
        "absolute",
        method,
        FrameMethodValue(values[2], current - baseline),
        FrameMethodValue(values[1], current - baseline),
        outputs,
        "zero_total" if current == baseline else "defined",
    )
    facts = FrameMethodFacts(
        kind="attribute",
        origin_result_id="result_worker_method",
        output_schema=schema,
        coordinates=(),
        parent_axis_count=0,
        parents=() if empty else (FrameMethodParent((), (metric,)),),
        targets=(FrameMethodTarget(metric.target, metric.kind, metric.outputs),),
        parent_schema=DataSchema((), ()),
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
                schema.columns,
                (
                    current - baseline,
                    current - baseline,
                    current - baseline,
                    1.0,
                    current,
                    baseline,
                    current - baseline,
                    True,
                    True,
                ),
                strict=True,
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
