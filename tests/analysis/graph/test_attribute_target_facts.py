"""Closed one-endpoint records preserve reread and historical totals independently."""

from dataclasses import fields, replace

import pytest

from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.core.frame_method_facts import (
    FrameAttributeMetric,
    FrameLevelAdditiveMethod,
    FrameLevelSupport,
    FrameMethodFacts,
    FrameMethodOutput,
    FrameMethodParent,
    FrameMethodTarget,
    FrameMethodValue,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization.terminal_protocol import (
    decode_method_record,
    encode_method_record,
)
from marivo.analysis.methods.frame_method_facts import validate_method_facts


def level_facts() -> FrameMethodFacts:
    roles = ("contribution", "total", "observed_total", "share", "value")
    columns = tuple(
        DataColumn(
            role,
            "sales." + role,
            ScalarDataType("float64"),
            True,
            "1" if role == "share" else "USD",
        )
        for role in roles
    )
    presence = DataColumn("present", "sales.present", ScalarDataType("boolean"), False)
    schema = DataSchema(
        (),
        columns,
        (presence,),
        value_paths=tuple(("sales", role) for role in roles),
        presence_paths=(("sales", "present"),),
    )
    method = FrameLevelAdditiveMethod(
        "level_additive",
        FrameMethodValue(
            DataColumn("parent", "parent", ScalarDataType("float64"), True, "USD"), 240.0
        ),
        (FrameLevelSupport("sales", "rows", 3, 3),),
    )
    metric = FrameAttributeMetric(
        ("sales",),
        "level",
        method,
        FrameMethodValue(columns[2], 220.0),
        FrameMethodValue(columns[1], 240.0),
        (
            FrameMethodOutput("contribution", ("sales", "contribution")),
            FrameMethodOutput("total", ("sales", "total")),
            FrameMethodOutput("observed_total", ("sales", "observed_total")),
            FrameMethodOutput("share", ("sales", "share")),
            FrameMethodOutput("value", ("sales", "value")),
            FrameMethodOutput("present", ("sales", "present")),
        ),
        "defined",
    )
    return FrameMethodFacts(
        "attribute",
        "reread-sales",
        schema,
        (),
        0,
        (FrameMethodParent((), (metric,)),),
        targets=(FrameMethodTarget(metric.target, metric.kind, metric.outputs),),
    )


def test_level_facts_keep_single_endpoint_and_independent_observed_total() -> None:
    facts = level_facts()
    metric = facts.parents[0].metrics[0]
    assert isinstance(metric, FrameAttributeMetric)
    assert tuple(field.name for field in fields(metric.method)) == ("kind", "value", "support")
    assert metric.observed_total.value == 220.0
    assert metric.total.value == 240.0
    validate_method_facts(facts)
    record = encode_method_record(facts.parents[0])
    assert decode_method_record(record, FrameMethodParent) == facts.parents[0]
    assert '"right"' not in record and '"right_input_count"' not in record
    assert "SourceFrameBinding" not in record


def test_target_facts_reject_wrong_mappings_and_inconsistent_reread_total() -> None:
    facts = level_facts()
    metric = facts.parents[0].metrics[0]
    assert isinstance(metric, FrameAttributeMetric)
    with pytest.raises(CoreRuleError):
        replace(metric, outputs=metric.outputs[:3])
    wrong = replace(metric, total=replace(metric.total, value=241.0))
    with pytest.raises(CoreRuleError):
        validate_method_facts(replace(facts, parents=(FrameMethodParent((), (wrong,)),)))
    wrong_path = replace(
        metric, outputs=(replace(metric.outputs[0], path=("missing",)), *metric.outputs[1:])
    )
    with pytest.raises(CoreRuleError):
        replace(facts, parents=(FrameMethodParent((), (wrong_path,)),))
    with pytest.raises(CoreRuleError):
        replace(metric, share_state="zero_total")


def test_level_facts_survive_independent_store_read_and_reject_changed_payload(tmp_path) -> None:
    import subprocess
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    import pandas as pd
    import pyarrow.parquet as pq

    from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
    from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.graph_reading import open_manifest

    facts = level_facts()
    local = DatasetRuntime.create(tmp_path, "one-endpoint-facts", timezone="UTC").local
    rows = pd.DataFrame(
        {
            "contribution": pd.Series([240.0], dtype="float64[pyarrow]"),
            "total": pd.Series([240.0], dtype="float64[pyarrow]"),
            "observed_total": pd.Series([220.0], dtype="float64[pyarrow]"),
            "share": pd.Series([1.0], dtype="float64[pyarrow]"),
            "value": pd.Series([240.0], dtype="float64[pyarrow]"),
            "present": pd.Series([True], dtype="bool[pyarrow]"),
        }
    )
    metadata = FrameResultMetadata(
        facts.origin_result_id,
        FrameAudit(
            (), EvaluationRecord(datetime(2026, 10, 21, tzinfo=timezone.utc), (), ("literal",))
        ),
    )
    saved = local.publish(
        CompletedFrameResult(CompletedFrameData(facts.output_schema, rows), metadata, (facts,))
    )
    script = """
import sys
from pathlib import Path
from unittest.mock import patch
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('cold method facts attempted source access')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden):
    local = LocalFrameServices(SessionStore.open_existing(Path(sys.argv[1])), sys.argv[2])
    saved = local.open_frame(sys.argv[3])
    facts = local.read_method_facts(saved)
    target = facts[0].parents[0].metrics[0]
    assert target.kind == 'level'
    assert target.observed_total.value == 220.0
    assert target.total.value == 240.0
    assert not hasattr(target.method, 'right')
    assert local.read_data(saved).copy_rows()['total'].tolist() == [240.0]
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), local.session_ref, saved.artifact_id],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    descriptor = open_manifest(local.store, local.session_ref, saved.artifact_id).method_facts[0]
    path = tmp_path / descriptor.payload.rows.receipt.project_relative_path / "data.parquet"
    payload = pq.read_table(path)
    altered = payload.to_pandas()
    altered.loc[0, "record"] = altered.loc[0, "record"].replace("240.0", "241.0")
    import pyarrow as pa

    pq.write_table(pa.Table.from_pandas(altered, schema=payload.schema, preserve_index=False), path)
    with pytest.raises(AnalysisError):
        local.read_method_facts(saved)


def test_empty_parent_facts_retain_and_validate_explicit_output_directory() -> None:
    facts = level_facts()
    empty = replace(facts, parents=())
    assert empty.targets == facts.targets
    with pytest.raises(CoreRuleError):
        replace(empty, targets=(replace(empty.targets[0], outputs=empty.targets[0].outputs[:3]),))
    share = facts.output_schema.values[3]
    bad = replace(
        facts.output_schema,
        values=(
            *facts.output_schema.values[:3],
            replace(share, unit="USD"),
            *facts.output_schema.values[4:],
        ),
    )
    with pytest.raises(CoreRuleError):
        replace(empty, output_schema=bad)
    contribution = facts.output_schema.values[0]
    bad = replace(
        facts.output_schema,
        values=(
            replace(contribution, dtype=ScalarDataType("boolean")),
            *facts.output_schema.values[1:],
        ),
    )
    with pytest.raises(CoreRuleError):
        replace(empty, output_schema=bad)


def test_level_linear_facts_reject_conflicting_shared_root_support() -> None:
    from marivo.analysis.core.frame_method_facts import FrameLevelLinearMethod, FrameLevelLinearTerm

    facts = level_facts()
    metric = facts.parents[0].metrics[0]
    assert isinstance(metric, FrameAttributeMetric)
    assert isinstance(metric.method, FrameLevelAdditiveMethod)
    child = metric.method
    conflicting = replace(child, support=(replace(child.support[0], effective_count=2),))
    linear = FrameLevelLinearMethod(
        "level_linear",
        child.value,
        (FrameLevelLinearTerm(1, child), FrameLevelLinearTerm(-1, conflicting)),
    )
    with pytest.raises(CoreRuleError):
        replace(metric, method=linear)
