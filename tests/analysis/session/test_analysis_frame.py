"""Private Frame data operations and lifecycle use the current fixed input."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from marivo.analysis.core.artifact_data import DataAxis, DataColumn, DataSchema
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    MemoryFrameBacking,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.dsl.column import row_count
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_sources import values
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.frame_services import LocalFrameServices
from tests.shared_fixtures import terminal_runs


@pytest.fixture
def local(tmp_path: Path) -> LocalFrameServices:
    return DatasetRuntime.create(tmp_path, "frame-lifecycle", timezone="UTC").local


def _memory(local: LocalFrameServices) -> AnalysisFrame:
    key = DataColumn("key.0", "region", ScalarDataType("string"), False)
    value = DataColumn("value.0", "value", ScalarDataType("int64"), True, "USD")
    presence = DataColumn("presence.0", "right_present", ScalarDataType("boolean"), False)
    schema = DataSchema(
        (DataAxis("axis.0", (key,), name="region"),),
        (value,),
        (presence,),
        value_paths=(("value",),),
        presence_paths=(("right_present",),),
    )
    data = CompletedFrameData(
        schema,
        pd.DataFrame({"key.0": ["B", "A"], "value.0": [10, 100], "presence.0": [False, True]}),
    )
    metadata = FrameResultMetadata(
        "original-result",
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
    )
    return AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))


def test_literal_compute_is_runless_and_memory_compute_returns_self(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import marivo.analysis.materialization.frame_evaluation as evaluator

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("source preparation is forbidden")

    monkeypatch.setattr(evaluator, "open_query_sources", forbidden)
    before_files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    pending = values(local, columns={"value": "USD"}, rows=(((), {"value": 7}),))
    before = tuple(terminal_runs(local))
    with pytest.raises(CoreRuleError, match="compute"):
        pending.to_pandas()
    memory = pending.compute()
    assert memory.compute() is memory
    assert memory._input_id != pending._input_id
    assert memory.to_pandas()[("value", "value")].tolist() == [7]
    assert tuple(terminal_runs(local)) == before
    assert isinstance(memory._backing, MemoryFrameBacking)
    assert memory._backing.metadata.audit.evaluation.operations == ("literal",)
    assert memory._backing.metadata.audit.source_facts == ()
    second = pending.compute()
    second.select({"twice": second["value"] * 2}).compute().to_pandas()
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before_files
    assert second._backing.metadata.result_id != memory._backing.metadata.result_id
    with pytest.raises(CoreRuleError, match="exact Frame input"):
        memory._bind_statistic(pending["value"].sum())


def test_current_rows_select_where_top_reduce_and_presence(local: LocalFrameServices) -> None:
    root = _memory(local)
    reduced = root.reduce({"mean": root["value"].mean(), "rows": row_count()}).compute()
    assert reduced.to_pandas()[("value", "mean")].tolist() == [55.0]
    assert reduced.to_pandas()[("value", "rows")].tolist() == [2]
    assert reduced.axes == () and reduced.presence_names == ()
    top = root.top(2, by=root["value"])
    filtered = top.where(top["value"] > 0)
    selected = filtered.select({"net": filtered["value"] - 1}).compute()
    assert selected.to_pandas()[("coordinate", "axis.0")].tolist() == ["A", "B"]
    assert selected.to_pandas()[("value", "net")].tolist() == [99, 9]
    assert selected.to_pandas()[("presence", "right_present")].tolist() == [True, False]
    assert selected.output_names == ("net",) and selected.presence_names == ("right_present",)
    grouped = root.reduce({"rows": row_count()}, by=root.axes).compute()
    assert grouped.presence_names == () and grouped.axes[0].name == "region"
    assert root.top(0, by=root["value"]).compute()._backing.data.row_count == 0
    empty = root.top(0, by=root["value"])
    count = empty.reduce({"rows": row_count()}).compute()
    assert count.to_pandas()[("value", "rows")].tolist() == [0]


def test_show_is_metadata_only_for_pending_and_n_zero(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _memory(local)
    calls: list[int] = []
    original = CompletedFrameData.preview_rows

    def preview(self: CompletedFrameData, n: int) -> pd.DataFrame:
        calls.append(n)
        return original(self, n)

    monkeypatch.setattr(CompletedFrameData, "preview_rows", preview)
    root.show(n=0)
    pending = root.select({"new": root["value"] + 1})
    pending.show(n=10)
    assert calls == []
    for count in (-1, True):
        with pytest.raises(CoreRuleError, match="nonnegative integer"):
            pending.show(n=count)
    root.show(n=1)
    assert calls == [1]
    assert "pending" in capsys.readouterr().out


def test_pending_show_names_current_operations_and_direct_input_kinds(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _memory(local)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("pending display must never read rows, attachments or execute")

    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "preview_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_attachments", forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden
    )
    for pending, operation, parameter in (
        (root.top(0, by=root["value"]), "top", "n=0"),
        (root.where(root["value"] > 3), "where", "3"),
        (root.select({"net": root["value"] - 1}), "select", "net"),
        (root.reduce({"mean": root["value"].mean()}, by=root.axes), "reduce", "axis.0"),
    ):
        pending.show(n=20)
        text = capsys.readouterr().out
        assert f"operation: {operation}" in text and parameter in text
        assert "inputs:" in text and "memory" in text
    literal = values(local, columns={"value": None}, rows=(((), {"value": 7}),))
    literal.show(n=0)
    assert "literal" in capsys.readouterr().out
    derived = literal.top(0, by=literal["value"])
    derived.show(n=0)
    assert "expression(literal)" in capsys.readouterr().out
    deep = derived.where(derived["value"] > 0)
    deep.show(n=0)
    assert "expression" in capsys.readouterr().out


def test_export_values_and_attrs_are_isolated(local: LocalFrameServices) -> None:
    root = _memory(local)
    exported = root.to_pandas()
    exported.loc[0, ("value", "value")] = 999
    exported.attrs["axes"][0]["name"] = "changed"
    assert root.to_pandas()[("value", "value")].tolist() == [10, 100]
    assert root.to_pandas().attrs["axes"][0]["name"] == "region"


def test_new_operations_reject_foreign_handles_and_invalid_names(local: LocalFrameServices) -> None:
    first, second = _memory(local), _memory(local)
    with pytest.raises(CoreRuleError, match="exact Store/Session/Frame"):
        first.select({"value": second["value"]})
    with pytest.raises(CoreRuleError, match="exact Frame input"):
        first.reduce({"mean": second["value"].mean()})
    with pytest.raises(CoreRuleError, match="name segment"):
        first.select({"left.value": first["value"]})
    with pytest.raises(CoreRuleError, match="reserved presence"):
        first.reduce({"right_present": row_count()})
