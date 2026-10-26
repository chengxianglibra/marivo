"""Private Frame data operations and lifecycle use the current fixed input."""

from dataclasses import replace
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
    import marivo.analysis.materialization.frame_source_preparation as evaluator

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
    assert isinstance(second._backing, MemoryFrameBacking)
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
    top_zero = root.top(0, by=root["value"]).compute()
    assert isinstance(top_zero._backing, MemoryFrameBacking)
    assert top_zero._backing.data.row_count == 0
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


def test_reduce_automatic_names_and_grouped_manual_values(local: LocalFrameServices) -> None:
    root = _memory(local)
    value = replace(root["value"], _name=None)
    statistics = (value.sum(), value.mean(), row_count())
    pending = root.reduce(statistics)
    assert pending.output_names == ("value_sum", "value_mean", "row_count")
    explicit = root.reduce(dict(zip(pending.output_names, statistics, strict=True)))
    result = pending.compute()
    table = result.to_pandas()
    assert table[("value", "value_sum")].tolist() == [110]
    assert table[("value", "value_mean")].tolist() == [55.0]
    assert table[("value", "row_count")].tolist() == [2]
    pd.testing.assert_frame_equal(table, explicit.compute().to_pandas())
    assert result._schema.values == explicit._schema.values
    assert result.axes == () and result.presence_names == ()
    for name, statistic in zip(pending.output_names, statistics, strict=True):
        single = root.reduce(statistic)
        assert single.output_names == (name,)
        assert (
            single.compute().to_pandas()[("value", name)].tolist()
            == table[("value", name)].tolist()
        )
    grouped = root.reduce(statistics, by=root.axes).compute()
    groups = grouped.to_pandas()
    assert grouped._schema.axes == root._schema.axes
    assert groups[("coordinate", "axis.0")].tolist() == ["A", "B"]
    assert groups[("value", "value_sum")].tolist() == [100, 10]
    assert groups[("value", "value_mean")].tolist() == [100.0, 10.0]
    assert groups[("value", "row_count")].tolist() == [1, 1]
    assert grouped.presence_names == ()
    repeated = result.reduce(result["value_sum"].sum())
    assert repeated.output_names == ("value_sum_sum",)


def test_reduce_automatic_names_preserve_empty_and_na_statistics(local: LocalFrameServices) -> None:
    root = _memory(local)
    empty = root.top(0, by=root["value"])
    summary = empty.reduce((empty["value"].sum(), empty["value"].mean(), row_count())).compute()
    assert summary.to_pandas().iloc[0].tolist() == [0, pd.NA, 0]
    nullable = root.select({"value": root["value"] / 0}).compute()
    result = nullable.reduce(
        (nullable["value"].sum(), nullable["value"].mean(), row_count())
    ).compute()
    assert result.output_names == ("value_sum", "value_mean", "row_count")
    assert result.to_pandas().iloc[0].tolist() == [pd.NA, pd.NA, 2]


def test_reduce_automatic_name_rejections_are_structured_and_runless(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _memory(local)
    aligned = root.align(root, on=((root.axes[0], root.axes[0]),))
    unnamed = (
        (root, (root["value"] + 1).sum()),
        (root, root["value"].corr(root["value"], method="pearson")),
        (root, root.over(root["value"].sum()).sum()),
        (aligned, aligned["left.value"].sum()),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("naming must not execute or read rows")

    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_evaluation.evaluate_frame", forbidden
    )
    before = tuple(terminal_runs(local))
    assert root.reduce(root["value"].sum()).output_names == ("value_sum",)
    for receiver, statistic in unnamed:
        with pytest.raises(CoreRuleError) as error:
            receiver.reduce(statistic)
        assert (
            "explicit name" in error.value.repair.action
            or "output name" in error.value.repair.action
        )
        assert error.value.repair.help_target.canonical_id == "analysis_frame.reduce"
        assert error.value.expected and error.value.received
        assert receiver.reduce({"your_name": statistic}).output_names == ("your_name",)
    for statistic in (row_count(), root["value"].sum()):
        with pytest.raises(CoreRuleError) as error:
            root.reduce((statistic, statistic))
        assert error.value.expected == "distinct statistic output names"
        assert "input positions 1 and 2" in error.value.received
        assert "explicit name" in error.value.repair.action
        assert root.reduce({"first": statistic, "second": statistic}).output_names == (
            "first",
            "second",
        )
    for invalid in (
        (),
        {},
        [],
        set(),
        iter(()),
        "value",
        1,
        (root["value"],),
        ({"name": row_count()},),
        (("name", row_count()),),
    ):
        with pytest.raises(CoreRuleError) as error:
            root.reduce(invalid)
        assert error.value.repair.help_target.canonical_id == "analysis_frame.reduce"
    assert tuple(terminal_runs(local)) == before


def test_reduce_automatic_names_preserve_exact_frame_and_session_ownership(
    local: LocalFrameServices, tmp_path: Path
) -> None:
    root = _memory(local)
    other_local = DatasetRuntime.create(tmp_path / "other", "other", timezone="UTC").local
    for other in (_memory(local), _memory(other_local)):
        with pytest.raises(CoreRuleError) as error:
            root.reduce(other["value"].sum())
        assert error.value.expected == "a statistic from this exact Frame input"
        assert "Reacquire" in error.value.repair.action
