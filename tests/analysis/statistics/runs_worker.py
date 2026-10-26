"""Source-offline calendar interval recipes consume committed pandas output."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import terminal_runs


def segments(frame: pd.DataFrame, threshold: float = 0) -> pd.DataFrame:
    """Find consecutive qualifying cells using the exported actual calendar bounds."""
    axes = frame.attrs["axes"]
    calendar = next(axis for axis in axes if axis["role"] == "calendar")
    calendar_column = calendar["name"]
    group_columns = [axis["name"] for axis in axes if axis["role"] in ("identity", "field")]
    partitions = (
        frame.groupby(group_columns, dropna=False, sort=False) if group_columns else (((), frame),)
    )
    records: list[dict[str, object]] = []
    for group, values in partitions:
        active: dict[str, object] | None = None
        for _, row in values.sort_values(calendar_column).iterrows():
            bound = {
                "actual_start": row[calendar_column + ".actual_start"],
                "actual_end": row[calendar_column + ".actual_end"],
            }
            value = row["value"]
            qualifies = not pd.isna(value) and value > threshold
            if qualifies:
                if active is None or active["end"] != bound["actual_start"]:
                    if active is not None:
                        records.append(active)
                    active = {
                        "group": group,
                        "start": bound["actual_start"],
                        "end": bound["actual_end"],
                        "count": 1,
                    }
                else:
                    active["end"] = bound["actual_end"]
                    count = active["count"]
                    assert isinstance(count, int)
                    active["count"] = count + 1
            elif active is not None:
                records.append(active)
                active = None
        if active is not None:
            records.append(active)
    result = pd.DataFrame.from_records(records, columns=("group", "start", "end", "count"))
    result["duration"] = result["end"] - result["start"]
    return result


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline recipe touched current Semantic/calendar or source")


def _author(root: Path) -> None:
    with duckdb.connect(str(root / "warehouse.duckdb")):
        pass
    semantic = root / "models/semantic/sales"
    semantic.mkdir(parents=True)
    datasource = root / "models/datasources"
    datasource.mkdir(parents=True)
    (root / "marivo.toml").write_text('[project]\nname="runs-export"\n')
    (datasource / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path='warehouse.duckdb')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Fixture', default=True)\n"
    )
    (semantic / "models.py").write_text("""from datetime import date
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.parquet("rows.parquet"), primary_key=["id"])
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
time_ref = ms.time_dimension_column(name="time", entity=orders, column="time", is_default=True)
calendar = ms.entity(name="calendar", datasource=ms.ref.datasource("warehouse"), source=md.parquet("calendar.parquet"), primary_key=["calendar_date"])
calendar_date = ms.time_dimension_column(name="calendar_date", entity=calendar, column="calendar_date")
period = ms.dimension_column(name="period", entity=calendar, column="period")
unequal = ms.period_calendar(name="unequal", date=calendar_date, boundary_timezone="UTC", coverage=(date(2026, 8, 1), date(2026, 8, 7)), levels={"period": period})
amount = amount_ref
time = time_ref
@ms.metric()
def maximum():
    return amount.max(time=time)
""")
    pq.write_table(
        pa.table(
            {
                "id": [1, 2, 3],
                "amount": [1, 2, 7],
                "time": pa.array(
                    [datetime(2026, 8, day, tzinfo=timezone.utc) for day in (1, 2, 4)],
                    type=pa.timestamp("us", tz="UTC"),
                ),
            }
        ),
        root / "rows.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "calendar_date": [f"2026-08-{day:02}" for day in range(1, 7)],
                "period": ["a", "b", "b", "c", "c", "c"],
            }
        ),
        root / "calendar.parquet",
    )


def run(root: Path, phase: str, calendar: bool = False) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    os.chdir(root)
    manifest_path = root / "runs-recovery.json"
    if phase == "produce":
        _author(root)
        catalog = ms.load(workspace_dir=root)
        if calendar:
            from tests.analysis.statistics.deviation_time_worker import publish_calendar

            publish_calendar(catalog, "UTC")
        session = mv.session.get_or_create("runs-export", timezone="UTC")
        scope = session.time_scope(
            start="2026-08-01", end="2026-08-07" if calendar else "2026-08-04"
        )
        grain = (
            catalog.get("period_calendar:sales.unequal").grain("period")
            if calendar
            else mv.grain("day")
        )
        result = session.observe(
            {"value": catalog.get("metric:sales.maximum").value}, by=(scope.by(grain),)
        ).persist()
        assert result.to_pandas()["value"].tolist() == [1, 2, 7]
        manifest_path.write_text(
            json.dumps({"session": session.id, "artifact": result.artifact.id})
        )
    else:
        manifest = json.loads(manifest_path.read_text())
        with (
            patch.object(ms, "load", forbidden),
            patch.object(SemanticProject, "load", forbidden),
            patch.object(mv.Session, "catalog", property(forbidden)),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "execute", forbidden),
        ):
            session = mv.session.resume(manifest["session"])
            result = session.persisted_frame(manifest["artifact"])
            frame = result.to_pandas()
            before = len(terminal_runs(session))
            selected = segments(frame, threshold=1)
            assert selected["count"].tolist() == [2]
            assert selected["duration"].tolist() == [timedelta(days=5 if calendar else 2)]
            assert segments(frame, threshold=2)["count"].tolist() == [1]
            assert len(terminal_runs(session)) == before
    assert session._runtime.store.resources(session.id) == ()


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], len(sys.argv) > 3 and sys.argv[3] == "calendar")
    print(json.dumps({"phase": sys.argv[2], "pid": os.getpid()}))
