"""Private Frame data operations and lifecycle use the current fixed input."""

import re
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
from marivo.analysis.core.lineage import empty as empty_lineage
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
    assert memory.to_pandas()["value"].tolist() == [7]
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
    assert reduced.to_pandas()["mean"].tolist() == [55.0]
    assert reduced.to_pandas()["rows"].tolist() == [2]
    assert reduced.axes == () and reduced.presence_names == ()
    top = root.top(2, by=root["value"])
    filtered = top.where(top["value"] > 0)
    selected = filtered.select({"net": filtered["value"] - 1}).compute()
    assert selected.to_pandas()[selected.axes[0].name.rsplit(".", 1)[-1]].tolist() == ["A", "B"]
    assert selected.to_pandas()["net"].tolist() == [99, 9]
    assert selected.to_pandas()["right_present"].tolist() == [True, False]
    assert selected.output_names == ("net",) and selected.presence_names == ("right_present",)
    grouped = root.reduce({"rows": row_count()}, by=root.axes).compute()
    assert grouped.presence_names == () and grouped.axes[0].name == "region"
    top_zero = root.top(0, by=root["value"]).compute()
    assert isinstance(top_zero._backing, MemoryFrameBacking)
    assert top_zero._backing.data.row_count == 0
    empty = root.top(0, by=root["value"])
    count = empty.reduce({"rows": row_count()}).compute()
    assert count.to_pandas()["rows"].tolist() == [0]


def test_empty_select_preserves_rows_order_coordinates_and_presence(
    local: LocalFrameServices,
) -> None:
    root = _memory(local)
    pending = root.select({})
    assert pending.output_names == ()
    assert pending.presence_names == ("right_present",)
    with pytest.raises(CoreRuleError, match="compute"):
        pending.to_pandas()
    result = pending.compute()
    table = result.to_pandas()
    assert table.columns.tolist() == ["region", "right_present"]
    assert table["region"].tolist() == ["B", "A"]
    assert table["right_present"].tolist() == [False, True]
    pd.testing.assert_frame_equal(result.select({}).compute().to_pandas(), table)
    assert root.output_names == ("value",)
    assert root.to_pandas()["value"].tolist() == [10, 100]


@pytest.mark.parametrize("invalid", (None, (), []))
def test_select_rejects_non_mapping_with_structured_repair(
    local: LocalFrameServices, invalid: object
) -> None:
    with pytest.raises(CoreRuleError) as caught:
        _memory(local).select(invalid)
    error = caught.value
    assert error.expected == "a named value mapping (possibly empty)"
    assert error.received == type(invalid).__name__
    assert error.location == "frame.select"
    assert error.repair is not None and "select({})" in error.repair.action
    assert error.repair.help_target.canonical_id == "analysis_frame.select"


def test_show_is_metadata_only_for_pending_and_n_zero(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from marivo.analysis.materialization import frame_paging
    from marivo.analysis.materialization.graph_reading import FramePreview

    root = _memory(local)
    calls: list[int] = []
    copied: list[pd.DataFrame] = []
    original = frame_paging.memory_preview

    def preview(data: CompletedFrameData, n: int) -> FramePreview:
        calls.append(n)
        result = original(data, n)
        copied.append(result.rows)
        return result

    monkeypatch.setattr(frame_paging, "memory_preview", preview)
    root.show(n=0)
    pending = root.select({"new": root["value"] + 1})
    pending.show(n=10)
    assert calls == []
    for count in (-1, True):
        with pytest.raises(CoreRuleError, match="nonnegative integer"):
            pending.show(n=count)
    for budget in (0, -1, True):
        with pytest.raises(ValueError, match="max_output_bytes"):
            root.show(n=10, max_output_bytes=budget)
    assert calls == []
    root.show(n=1)
    assert calls == [1]
    assert len(copied[0]) == 1
    assert copied[0]["key.0"].tolist() == ["B"]
    assert copied[0]["value.0"].tolist() == [10]
    assert copied[0]["presence.0"].tolist() == [False]
    assert "pending" in capsys.readouterr().out


def test_show_respects_row_and_byte_limits_with_exact_counts(
    local: LocalFrameServices, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = DataColumn("key", "cluster", ScalarDataType("string"), False)
    value = DataColumn("value", "value", ScalarDataType("int64"), False)
    schema = DataSchema(
        (DataAxis("cluster", (key,), name="cluster"),), (value,), value_paths=(("value",),)
    )
    data = CompletedFrameData(
        schema,
        pd.DataFrame({"key": [f"cluster-{i:02d}" for i in range(26)], "value": list(range(26))}),
    )
    frame = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            data, replace(_memory(local)._backing.metadata, lineage=empty_lineage(data.schema))
        ),
    )
    frame.show()
    default = capsys.readouterr().out
    assert "total=26 displayed=20 omitted=6" in default
    assert "cluster-19" in default and "cluster-20" not in default
    assert "preview limited to 20 rows" in default
    frame.show(max_output_bytes=None)
    row_limited = capsys.readouterr().out
    combined_budget = len(row_limited.encode("utf-8"))
    frame.show(max_output_bytes=combined_budget)
    combined = capsys.readouterr().out
    assert len(combined.encode("utf-8")) <= combined_budget
    assert "displayed=20" not in combined
    assert "preview limited to 20 rows" in combined
    assert f"output truncated at {combined_budget} bytes" in combined
    frame.show(n=26, max_output_bytes=None)
    complete = capsys.readouterr().out
    assert "total=26 displayed=26 omitted=0" in complete
    assert "cluster-25" in complete
    exact_budget = len(complete.encode("utf-8"))
    frame.show(n=26, max_output_bytes=exact_budget)
    assert capsys.readouterr().out == complete
    frame.show(n=26, max_output_bytes=exact_budget - 1)
    shorter = capsys.readouterr().out
    assert len(shorter.encode("utf-8")) <= exact_budget - 1
    assert f"output truncated at {exact_budget - 1} bytes" in shorter
    frame.show(n=26, max_output_bytes=900)
    bounded = capsys.readouterr().out
    assert len(bounded.encode("utf-8")) <= 900
    assert "output truncated" in bounded
    assert "total=26" in bounded and "omitted=" in bounded
    with pytest.raises(ValueError, match="minimum") as caught:
        frame.show(n=26, max_output_bytes=1)
    minimum_match = re.search(r"minimum is (\d+) bytes", str(caught.value))
    assert minimum_match is not None
    minimum = int(minimum_match[1])
    frame.show(n=26, max_output_bytes=minimum)
    recovered = capsys.readouterr().out
    assert len(recovered.encode("utf-8")) <= minimum
    assert "total=26" in recovered
    saved = frame.persist()
    requests: list[int] = []
    original = LocalFrameServices.preview

    def preview(self: LocalFrameServices, backing: object, n: int) -> object:
        requests.append(n)
        return original(self, backing, n)

    monkeypatch.setattr(LocalFrameServices, "preview", preview)
    saved.show(n=0)
    assert requests == []
    saved.show(n=26, max_output_bytes=None)
    assert requests == [26]
    assert "cluster-25" in capsys.readouterr().out


@pytest.mark.parametrize("empty", (False, True))
def test_show_metadata_only_preserves_row_counts_at_byte_limit(
    local: LocalFrameServices,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    empty: bool,
) -> None:
    frame = _memory(local)
    if empty:
        frame = frame.top(0, by=frame["value"]).compute()
    saved = frame.persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata-only display must not read row or coordinate payloads")

    monkeypatch.setattr(CompletedFrameData, "preview_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "copy_attachments", forbidden)
    monkeypatch.setattr(LocalFrameServices, "preview", forbidden)
    for root in (frame, saved):
        root.show(n=20 if empty else 0, max_output_bytes=500)
        output = capsys.readouterr().out
        assert len(output.encode("utf-8")) <= 500
        assert (
            "total=0 displayed=0 omitted=0" in output
            if empty
            else ("total=2 displayed=0 omitted=2" in output)
        )
        assert "empty result" in output if empty else "n=0" in output
        with pytest.raises(ValueError, match="minimum"):
            root.show(n=20 if empty else 0, max_output_bytes=1)


def test_show_unlimited_preserves_wide_rows_and_escaped_cells(
    local: LocalFrameServices, capsys: pytest.CaptureFixture[str]
) -> None:
    long_value = "界" * 300 + "\\line|one\nnext"
    values = tuple(
        DataColumn(f"v{i}", f"value{i}", ScalarDataType("string"), False) for i in range(10)
    )
    schema = DataSchema((), values, value_paths=tuple((value.name,) for value in values))
    data = CompletedFrameData(
        schema, pd.DataFrame({f"v{i}": [long_value if i == 9 else str(i)] for i in range(10)})
    )
    frame = AnalysisFrame._from_memory(
        local,
        CompletedFrameResult(
            data, replace(_memory(local)._backing.metadata, lineage=empty_lineage(data.schema))
        ),
    )
    frame.show(n=1, max_output_bytes=None)
    output = capsys.readouterr().out
    assert "value9" in output and "界" * 300 in output
    assert r"\\line\|one\nnext" in output
    assert "total=1 displayed=1 omitted=0" in output


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
    exported.loc[0, "value"] = 999
    exported.attrs["axes"][0]["name"] = "changed"
    assert root.to_pandas()["value"].tolist() == [10, 100]
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
    assert table["value_sum"].tolist() == [110]
    assert table["value_mean"].tolist() == [55.0]
    assert table["row_count"].tolist() == [2]
    pd.testing.assert_frame_equal(table, explicit.compute().to_pandas())
    assert result._schema.values == explicit._schema.values
    assert result.axes == () and result.presence_names == ()
    for name, statistic in zip(pending.output_names, statistics, strict=True):
        single = root.reduce(statistic)
        assert single.output_names == (name,)
        assert single.compute().to_pandas()[name].tolist() == table[name].tolist()
    grouped = root.reduce(statistics, by=root.axes).compute()
    groups = grouped.to_pandas()
    assert grouped._schema.axes == root._schema.axes
    assert groups["region"].tolist() == ["A", "B"]
    assert groups["value_sum"].tolist() == [100, 10]
    assert groups["value_mean"].tolist() == [100.0, 10.0]
    assert groups["row_count"].tolist() == [1, 1]
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


def test_flat_export_aliases_conflicts_and_preview(
    local: LocalFrameServices, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _memory(local)
    frame = root.select({"region": root["value"]}).compute()
    with pytest.raises(CoreRuleError) as caught:
        frame.to_pandas()
    assert caught.value.expected == "unique flat export column names"
    assert "axis_names" in caught.value.repair.action
    rows = frame.to_pandas(axis_names={frame.axes[0].token: "customer_region"})
    assert rows.columns.tolist() == ["customer_region", "region", "right_present"]
    assert rows["customer_region"].tolist() == ["B", "A"]
    assert rows["region"].tolist() == [10, 100]
    assert rows["right_present"].tolist() == [False, True]
    frame.show(n=1)
    preview = capsys.readouterr().out
    assert "region [coordinate axis.0]" in preview and "region [value]" in preview
    with pytest.raises(CoreRuleError, match="unambiguous current axis"):
        frame.to_pandas(axis_names={root.axes[0]: "customer_region"})
    for name in ("", " region", "region\n"):
        with pytest.raises(CoreRuleError, match="printable export name"):
            root.to_pandas(axis_names={root.axes[0].token: name})


def test_export_alias_validation_precedes_stored_payload_reads(
    local: LocalFrameServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _memory(local)
    frame = root.select({"region": root["value"]}).persist()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid export must not read stored data")

    monkeypatch.setattr(LocalFrameServices, "read_data", forbidden)
    with pytest.raises(CoreRuleError, match="unique flat export column names"):
        frame.to_pandas()
    with pytest.raises(CoreRuleError, match="unambiguous current axis"):
        frame.to_pandas(axis_names={root.axes[0]: "name"})


def test_string_export_alias_survives_compute_and_rejects_invalid_requests(
    local: LocalFrameServices,
) -> None:
    root = _memory(local)
    aliases = {root.axes[0].name: "member"}
    pending = root.select({"amount": root["value"]})
    with pytest.raises(CoreRuleError, match="complete memory or stored Frame"):
        pending.to_pandas(axis_names=aliases)
    fixed = pending.compute()
    assert fixed.to_pandas(axis_names=aliases).columns.tolist() == [
        "member",
        "amount",
        "right_present",
    ]
    for bad in ({"unknown.axis": "member"}, {fixed.axes[0]: "member"}):
        with pytest.raises(CoreRuleError, match="unambiguous current axis"):
            fixed.to_pandas(axis_names=bad)
    for member in (fixed.to_pandas, fixed.read_page):
        with pytest.raises(CoreRuleError, match="boolean time_details"):
            member(time_details="yes")


@pytest.mark.parametrize("member", ["to_pandas", "read_page"])
def test_axis_alias_errors_distinguish_keys_values_and_foreign_axes(
    local: LocalFrameServices, member: str
) -> None:
    frame = _memory(local)
    export = getattr(frame, member)
    with pytest.raises(CoreRuleError) as current:
        export(axis_names={frame.axes[0]: "bucket"})
    assert current.value.location.endswith(".axis_names.key")
    assert "{'axis.0': 'bucket'}" in current.value.repair.action
    other = _memory(local)
    with pytest.raises(CoreRuleError) as foreign:
        export(axis_names={other.axes[0]: "bucket"})
    assert "Reacquire" in foreign.value.repair.action
    assert "{'axis.0':" not in foreign.value.repair.action
    with pytest.raises(CoreRuleError) as value:
        export(axis_names={"axis.0": " invalid "})
    assert value.value.location.endswith(".axis_names['axis.0']")


def test_ordinary_time_coordinate_exports_one_value_with_aliases(local: LocalFrameServices) -> None:
    from marivo.analysis.core.data_types import TimestampDataType

    key = DataColumn("time", "event_date", TimestampDataType("us", "UTC"), False)
    value = DataColumn("amount", "amount", ScalarDataType("int64"), True)
    schema = DataSchema(
        (DataAxis("axis.0", (key,), name="sales.events.event_date"),),
        (value,),
        value_paths=(("amount",),),
    )
    times = pd.Series(["2026-01-01T12:00:00Z"], dtype="timestamp[us, tz=UTC][pyarrow]")
    data = CompletedFrameData(schema, pd.DataFrame({"time": times, "amount": [7]}))
    metadata = FrameResultMetadata(
        "time-export",
        FrameAudit((), EvaluationRecord(datetime(2026, 1, 1, tzinfo=timezone.utc), (), ())),
    )
    frame = AnalysisFrame._from_memory(local, CompletedFrameResult(data, metadata))
    for details in (False, True):
        rows = frame.to_pandas(time_details=details)
        assert rows.columns.tolist() == ["event_date", "amount"]
        assert rows["event_date"].tolist() == times.tolist()
        renamed = frame.to_pandas(axis_names={"axis.0": "event"}, time_details=details)
        assert renamed.columns.tolist() == ["event", "amount"]
        assert (
            frame.read_page(axis_names={"axis.0": "event"}, time_details=details)
            .to_pandas()["event"]
            .tolist()
            == times.tolist()
        )
