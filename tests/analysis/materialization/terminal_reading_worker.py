"""Independent producer and source-offline public terminal reading processes."""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import marivo.analysis as mv
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import terminal_runs


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("cold output reading must not load current semantics, sources or a graph")


def period_produce(root: Path, name: str) -> None:
    import duckdb

    with duckdb.connect(str(root / "warehouse.duckdb")) as database:
        database.execute("DELETE FROM orders")
        database.execute(
            "INSERT INTO orders VALUES "
            "(1,'A',100,'2026-01-01 00:00:00+00'),"
            "(2,'A',20,'2026-01-02 00:00:00+00'),"
            "(3,'A',0,'2026-02-01 00:00:00+00'),"
            "(4,'A',180,'2027-01-01 00:00:00+00'),"
            "(5,'A',30,'2027-02-01 00:00:00+00')"
        )
    session = mv.session.create(name, timezone="UTC")
    amount = session.catalog.fields.get("sales.orders.amount")
    stamp = session.catalog.fields.get("sales.orders.stamp")
    periods = session.time_scope(start="2027-01-01", end="2027-03-01").by(mv.grain("month"))
    metric = amount.sum(time=stamp)
    current = session.observe({"total": metric}, by=(periods,))
    baseline = session.observe({"total": metric}, by=(periods.shift(mv.offset(years=-1)),))
    pending_report = current.compare({"base": baseline}).compute()
    memory_current, memory_baseline = current.compute(), baseline.compute()
    memory_report = memory_current.compare({"base": memory_baseline}).compute()
    saved_current, saved_baseline = memory_current.persist(), memory_baseline.persist()
    stored_report = saved_current.compare({"base": saved_baseline}).compute()
    for report in (pending_report, memory_report, stored_report):
        rows = report.to_pandas()
        assert rows["value", "total"].tolist() == [180, 30]
        assert rows["value", "base.baseline.total"].tolist() == [120, 0]
        assert rows["value", "base.absolute.total"].tolist() == [60, 30]
        assert rows["value", "base.relative.total"].iloc[0] == 0.5
        assert pd.isna(rows["value", "base.relative.total"].iloc[1])
        assert rows["presence"].all().all()
        assert report._schema == pending_report._schema
    saved_report = stored_report.persist()
    print(
        session.id, saved_current.artifact.id, saved_baseline.artifact.id, saved_report.artifact.id
    )


def period_read(name: str, identities: list[str]) -> None:
    from marivo import _timezones
    from marivo.analysis.core import frame_calendar
    from marivo.analysis.materialization import frame_source_preparation

    with ExitStack() as patches:
        for owner, attribute, replacement in (
            (Session, "catalog", property(forbidden)),
            (SourceSession, "__init__", forbidden),
            (SourceSession, "execute", forbidden),
            (frame_source_preparation, "open_query_sources", forbidden),
            (frame_calendar, "freeze_calendar", forbidden),
        ):
            patches.enter_context(patch.object(owner, attribute, replacement))
        session = mv.session.resume(name)
        # Session admission validates its timezone name. Frozen Frame reads and
        # continuations below must not resolve that timezone authority again.
        patches.enter_context(patch.object(_timezones, "ZoneInfo", forbidden))
        current, baseline, report = (session.persisted_frame(value) for value in identities)
        before = len(terminal_runs(session))
        recomputed = current.compare({"base": baseline}).compute()
        assert recomputed._schema == report._schema
        pd.testing.assert_frame_equal(recomputed.to_pandas(), report.to_pandas())
        rows = report.to_pandas()
        assert rows["value", "total"].tolist() == [180, 30]
        assert rows["value", "base.baseline.total"].tolist() == [120, 0]
        assert rows["value", "base.absolute.total"].tolist() == [60, 30]
        assert rows["value", "base.relative.total"].iloc[0] == 0.5
        assert pd.isna(rows["value", "base.relative.total"].iloc[1])
        assert report.presence_names == ("current.present", "base.present")
        assert rows["presence"].all().all()
        directories = rows.attrs["frozen_coordinates"]
        current_directory = next(
            part["rows"] for part in directories if part["name"] == "calendar.axis.0"
        )
        assert current_directory["actual_start_civil"].tolist() == [
            "2027-01-01T00:00:00.000000+00:00",
            "2027-02-01T00:00:00.000000+00:00",
        ]
        assert current_directory["actual_start"].tolist() == [
            pd.Timestamp("2027-01-01T00:00:00+00:00"),
            pd.Timestamp("2027-02-01T00:00:00+00:00"),
        ]
        baseline.show(n=1)
        report.show(n=1)
        positive = report.where(report["base.absolute.total"] > 30)
        selected = positive.select({"delta": positive["base.absolute.total"]}).compute()
        assert selected.to_pandas()["value", "delta"].tolist() == [60]
        literal = report.select({"constant": 1}).compute()
        assert literal._schema.comparisons[0].values == ()
        assert literal.presence_names == report.presence_names
        reduced = selected.reduce({"total_delta": selected["delta"].sum()}).compute()
        assert reduced.to_pandas()["value", "total_delta"].tolist() == [60]
        assert reduced._schema.comparisons == () and reduced.presence_names == ()
        assert len(terminal_runs(session)) == before
    print("period-cold-ok")


def main() -> None:
    assert Path(mv.__file__).resolve().parents[2] == Path(__file__).resolve().parents[3], (
        "worker imported a different checkout"
    )
    root, name, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    os.chdir(root)
    if mode == "period-produce":
        period_produce(root, name)
        return
    if mode == "period-read":
        period_read(name, sys.argv[4:])
        return
    if mode == "produce":
        session = mv.session.create(name, timezone="UTC")
        amount = session.catalog.fields.get("sales.orders.amount")
        region = session.catalog.fields.get("sales.orders.region")
        artifact = session.observe(
            {"total": amount.sum(), "mean": amount.mean()}, by=(region,)
        ).persist()
        rows = artifact.to_pandas()
        assert {
            (None if key is None or key is pd.NA else key): value
            for key, value in zip(
                rows[("coordinate", "axis.0")], rows[("value", "total")], strict=True
            )
        } == {None: 5, "A": 9007199254740993}
        print(session.id, artifact.artifact.id)
        return
    with ExitStack() as patches:
        for owner, attribute, replacement in (
            (Session, "catalog", property(forbidden)),
            (SourceSession, "__init__", forbidden),
            (SourceSession, "execute", forbidden),
        ):
            patches.enter_context(patch.object(owner, attribute, replacement))
        session = mv.session.resume(name)
        before = len(terminal_runs(session))
        artifact = session.persisted_frame(sys.argv[4])
        assert artifact.artifact.id in repr(artifact.artifact)
        assert "backing=stored" in repr(artifact) and ".show()" in repr(artifact)
        artifact.show(n=1)
        frame = artifact.to_pandas()
        assert {
            (None if key is None or key is pd.NA else key): value
            for key, value in zip(
                frame[("coordinate", "axis.0")], frame[("value", "total")], strict=True
            )
        } == {None: 5, "A": 9007199254740993}
        assert dict(frame.attrs["units"])["total"] == "CNY"
        assert artifact.artifact.findings().items == ()
        facts = artifact.artifact.evidence_digest()
        assert any(item.kind == "read" for item in facts.items)
        facts.show()
        published = terminal_runs(session, status="succeeded")
        assert len(published) == 1
        assert published[0].lifecycle == "succeeded"
        assert published[0].output_artifact_ref == artifact.artifact.id
        assert len(terminal_runs(session)) == before
        assert session._runtime.store.resources(session.id) == ()
        fixed = artifact.compute()
        positive = artifact.where(artifact["total"] > 0).compute()
        assert len(positive.to_pandas()) == 2
        comparison = artifact.compare(
            {"base": artifact}, on={"base": ((artifact.axes[0], artifact.axes[0]),)}
        ).compute()
        assert comparison.to_pandas()[("value", "base.absolute.total")].tolist() == [0, 0]
        reduced = artifact.reduce({"rows": mv.row_count()}).compute()
        assert reduced.to_pandas()[("value", "rows")].tolist() == [2]
        assert len(terminal_runs(session)) == before
        copied = fixed.persist()
        assert isinstance(copied._backing, StoredFrameBacking)
        assert isinstance(fixed._backing, MemoryFrameBacking)
        assert copied._backing.metadata == fixed._backing.metadata
        assert len(terminal_runs(session)) == before + 1
        print("public-cold-ok")


if __name__ == "__main__":
    main()
