"""Terminal display and pandas dense ranking with independent raw-fact oracles."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.data_schema import CalendarDataAxis
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.analysis.core.frozen_calendar import read_calendar_directory
from marivo.analysis.dsl.source_capture import ValueRow
from marivo.analysis.materialization.graph_reading import (
    ExportRead,
    OutputRead,
    PreviewRead,
    ReadLayout,
)
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.packaging.graph_journeys import _create, _frame, _offline, _remove_sources
from tests.shared_fixtures import analysis_dsl_rows, run_ids
from tests.support.json import Json, checked, encode, obj, read


def _table(values: mv.AnalysisFrame, categories: mv.AnalysisFrame) -> pd.DataFrame:
    completed = values.compute()
    assert isinstance(completed._backing, MemoryFrameBacking)
    schema = completed._backing.data.schema
    calendar = next(i for i, axis in enumerate(completed.axes) if axis.role == "calendar")
    assert len(schema.axes) == 2
    axis = schema.axes[calendar]
    assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
    attachment = next(
        part
        for part in completed._backing.data.copy_attachments()
        if part.name == axis.frozen.directory_name
    )
    directory = read_calendar_directory(axis, attachment.schema, attachment.rows)
    bounds = {bucket.key[0][1]: bucket.nominal_start for bucket in directory.buckets}
    exported = completed.to_pandas()
    observed = _frame(completed, frame=exported)
    observed[f"key_{calendar}"] = observed[f"key_{calendar}"].map(bounds)
    frame = observed.rename(
        columns={f"key_{1 - calendar}": "key_0", f"key_{calendar}": "key_1", "value": "amount"}
    ).merge(
        _frame(categories).rename(columns={"value": "zone"}),
        on="key_0",
        validate="many_to_one",
    )
    frame["rank"] = (
        frame.groupby("zone", dropna=False)
        .amount.rank(method="dense", ascending=False)
        .astype("int64")
    )
    return frame.sort_values(["key_0", "key_1"]).reset_index(drop=True)


def _verify(frame: pd.DataFrame) -> Json:
    facts = analysis_dsl_rows("j2")
    zones: dict[str, str] = {}
    for member, zone in facts.customers:
        assert isinstance(member, str) and isinstance(zone, str)
        zones[member] = zone
    counts = {
        (member, month): sum(
            buyer == member and str(day).startswith(month)
            for _, buyer, _, _, day, _ in facts.orders
        )
        for member in zones
        for month in ("2026-07", "2026-08", "2026-09")
    }
    ranks = {
        key: sorted(
            {value for (member, _), value in counts.items() if zones[member] == zones[key[0]]},
            reverse=True,
        ).index(value)
        + 1
        for key, value in counts.items()
    }
    assert len(frame) == 12
    for row in frame.itertuples(index=False):
        assert isinstance(row.key_0, str)
        key = (row.key_0, str(row.key_1)[:7])
        assert row.amount == counts[key] and row.zone == zones[row.key_0]
        assert row.rank == ranks[key]
    return checked(json.loads(frame.to_json(orient="records", date_format="iso")))


def journey(phase: str, project: Path) -> dict[str, object]:
    """Produce source outputs, then read identical receipts in two source-free processes."""
    assert phase in ("produce", "fixed", "cold")
    project = project.resolve()
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    state_path = project / "display.json"
    if phase == "produce":
        session = _create(project, "j2", "parquet")
        members = session.members(ms.ref.entity("operations.device"))
        grid = session.time_scope(start="2026-07-01", end="2026-10-01").by(mv.grain("month"))
        values = session.observe(
            {"value": session.catalog.metrics.get("operations.reading_count")},
            during=grid,
            by=members,
            members=members,
        ).persist()
        categories = session.read(
            members, {"value": session.catalog.fields.get("operations.device.zone")}
        ).persist()
        frame = _table(values, categories)
        _verify(frame)
        rows: list[ValueRow] = []
        for row in frame.itertuples(index=False):
            assert isinstance(row.key_0, str) and isinstance(row.key_1, datetime)
            assert isinstance(row.zone, str)
            assert isinstance(row.amount, (int, float)) and isinstance(row.rank, (int, float))
            point = row.key_1.to_pydatetime() if isinstance(row.key_1, pd.Timestamp) else row.key_1
            rows.append(
                (
                    (row.key_0, point),
                    {"amount": int(row.amount), "zone": row.zone, "rank": int(row.rank)},
                )
            )
        # These explicit field keys come from observed calendar bounds, not a hand-authored calendar.
        table = session.values(
            by=(
                session.catalog.fields.get("operations.reading.device_id"),
                session.catalog.fields.get("operations.reading.recorded_at"),
            ),
            columns={"amount": None, "zone": None, "rank": None},
            rows=tuple(rows),
        ).persist()
        artifacts = {"values": values, "categories": categories, "table": table}
        state: dict[str, Json] = {
            "session": session.id,
            "artifacts": {name: value.artifact.id for name, value in artifacts.items()},
            "snapshots": {name: snapshot(value) for name, value in artifacts.items()},
        }
        state_path.write_bytes(encode(state))
        _remove_sources(project)
    else:
        state = read(state_path)
        identity = state["session"]
        assert isinstance(identity, str)
        with _offline(project):
            session = mv.session.resume(identity, by="id")
            before = run_ids(session)
            artifacts = {}
            for name, reference in obj(state["artifacts"]).items():
                assert isinstance(reference, str)
                value = session.persisted_frame(reference)
                assert snapshot(value) == obj(state["snapshots"])[name]
                artifacts[name] = value
            assert run_ids(session) == before
    values, categories, table = (artifacts[name] for name in ("values", "categories", "table"))
    with _offline(project):
        before = run_ids(session)
        rebuilt = _table(values, categories)
        rows_result = _verify(_frame(table))
        assert _verify(rebuilt) == rows_result
        output = io.StringIO()
        decoded: list[int] = []
        original_read = OutputRead.read

        def counted(
            reader: OutputRead, layout: ReadLayout, request: PreviewRead | ExportRead
        ) -> pd.DataFrame:
            result = original_read(reader, layout, request)
            decoded.append(len(result))
            return result

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(OutputRead, "read", counted)
            with contextlib.redirect_stdout(output):
                table.show(n=2)
        text = output.getvalue()
        assert len(text.encode()) <= 8192 and "rows: 12" in text and ".to_pandas()" in text
        assert "required_parts" not in text and "cell_tag" not in text and "call=" not in text
        assert isinstance(table._backing, StoredFrameBacking)
        assert decoded == [2] and table._backing.data.rows.receipt.realized_row_count == 12
        assert run_ids(session) == before and session._runtime.store.resources(session.id) == ()
    return {
        "pid": os.getpid(),
        "session": session.id,
        "rows": rows_result,
        "display": text,
        "continuation": checked(
            json.loads(
                rebuilt.sort_values(["rank", "key_0", "key_1"])
                .head(4)
                .to_json(orient="records", date_format="iso")
            )
        )
        if phase != "produce"
        else None,
        "run_count": len(run_ids(session)),
    }


if __name__ == "__main__":
    from tests.packaging.wheel_probe import assert_installed_origin

    assert_installed_origin()
    report = journey(sys.argv[1], Path(sys.argv[2]))
    report["origin"] = assert_installed_origin()
    Path(sys.argv[3]).write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
