"""Native Entity count, complete domains and terminal recovery witnesses."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value
from tests.support.paths import PROJECT_ROOT


def _data(
    backend: str, key_type: Literal["int64", "string"], *, row_count: int = 1000
) -> SourceData:
    entries: list[dict[str, object]] = []
    values: list[str] = []
    for index in range(1, row_count + 1):
        identity = str(index) if key_type == "string" else index
        category = "even" if index % 2 == 0 else "odd"
        point = datetime(2026, 9, 2 if index == row_count else 1, tzinfo=timezone.utc)
        entries.append({"id": identity, "category": category, "happened": point})
        literal = repr(point.replace(tzinfo=None).isoformat(sep=" "))
        values.append(
            f"({identity!r},{category!r},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + literal
            + ")"
        )
    identity_type = "VARCHAR(10)" if key_type == "string" else "BIGINT"
    time_type = "TIMESTAMP(6) WITH TIME ZONE" if backend == "trino" else "TIMESTAMP(6)"
    return SourceData(
        f"id {identity_type}, category VARCHAR(10), happened {time_type}",
        ",".join(values),
        f"id {'String' if key_type == 'string' else 'Int64'}, category String, happened DateTime64(6, 'UTC')",
        entries,
    )


def _cold_outputs(root: Path, session_id: str, expected: int, artifacts: tuple[str, ...]) -> None:
    script = """
import sys
from unittest.mock import patch
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('terminal Count output accessed Semantic or a source')
with patch.object(ms,'load',forbidden), patch.object(SourceSession,'__enter__',forbidden), patch.object(SourceSession,'execute',forbidden):
    session = mv.session.resume(sys.argv[1])
    before = len(terminal_runs(session))
    for artifact in sys.argv[3:]:
        frame = session.persisted_frame(artifact).to_pandas()
        assert frame['value'].sum() == int(sys.argv[2])
    assert len(terminal_runs(session)) == before
"""
    outcome = subprocess.run(
        [sys.executable, "-c", script, session_id, str(expected), *artifacts],
        cwd=root,
        env={**os.environ, "MARIVO_PROJECT_ROOT": str(root), "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


@pytest.mark.release
@pytest.mark.parametrize(
    ("backend", "profile", "key_type"), [("duckdb", "table", "int64"), ("duckdb", "view", "string")]
)
def test_native_count_rollup_and_group_state(
    backend: str,
    profile: str,
    key_type: Literal["int64", "string"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    row_count = 10
    first_day_count = row_count - 1
    with source_case(
        backend, profile, tmp_path, monkeypatch, _data(backend, key_type, row_count=row_count)
    ) as case:
        assert isinstance(case.source, TableSourceIR)
        arguments: dict[str, object] = dict(case.session.datasource.fields)
        arguments.update(
            {key + "_env": value for key, value in case.session.datasource.env_refs.items()}
        )
        user = arguments.pop("user", None)
        if user is not None:
            monkeypatch.setenv("MARIVO_COUNT_READER", str(user))
            arguments["user_env"] = "MARIVO_COUNT_READER"
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                + ")\n",
                "counts/_domain.py": "import marivo.semantic as ms\nms.domain(name='counts',owner='Analytics',default=True)\n",
                "counts/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "category=ms.dimension_column(name='category',entity=events,column='category')\nhappened=ms.time_dimension_column(name='happened',entity=events,column='happened',parse=ms.timestamp(timezone='UTC'),is_default=True)\nevents_value=events\nclock=happened\n@ms.metric()\ndef event_count():\n    return events_value.count(time=clock)\n",
            }
        )
        session = mv.session.get_or_create("native-count", timezone="UTC")
        metric = session.catalog.get("metric:counts.event_count")
        category = catalog_value(session.catalog, "counts.events.category", kind="field")
        members = session.members(session.catalog.get("entity:counts.events"))
        window = session.time_scope(start="2026-09-01", end="2026-09-02")
        scalar = session.observe({"value": metric}, by=(window,))
        individual = session.observe({"value": metric}, by=mv.product(window, members))
        grouped = session.observe({"value": metric}, by=(window, category))
        reads: list[tuple[CompiledRead, pd.DataFrame]] = []
        execute = SourceSession.execute

        def captured(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            frame = execute(owner, read)
            reads.append((read, frame))
            return frame

        monkeypatch.setattr(SourceSession, "execute", captured)
        result = scalar.persist()
        assert result.to_pandas()["value"].tolist() == [first_day_count]
        aggregates = [
            frame
            for read, frame in reads
            if read.purpose.startswith("query.") and "COUNT(" in read.sql.upper()
        ]
        assert [len(frame) for frame in aggregates] == [1]
        offset = len(reads)
        saved_groups = grouped.persist()
        frame = saved_groups.to_pandas()
        assert dict(zip(frame[ordinary_columns(frame)[0]], frame["value"], strict=True)) == {
            "even": 4,
            "odd": 5,
        }
        aggregates = [
            frame
            for read, frame in reads[offset:]
            if read.purpose.startswith("query.") and "COUNT(" in read.sql.upper()
        ]
        assert [len(frame) for frame in aggregates] == [2]
        assert grouped.reduce({"value": grouped["value"].sum()}).persist().to_pandas()[
            "value"
        ].tolist() == [first_day_count]
        assert individual.reduce({"value": individual["value"].sum()}).persist().to_pandas()[
            "value"
        ].tolist() == [first_day_count]
        if key_type == "int64":
            data = saved_groups._local._manifest(saved_groups._require_stored("artifact_id")).data
            forged = mv.AnalysisFrame._from_stored(
                saved_groups._local,
                replace(data, rows=replace(data.rows, payload_digest="0" * 64)),
                artifact_id=saved_groups.artifact_id,
                metadata=saved_groups._local._manifest(
                    saved_groups._require_stored("artifact_id")
                ).metadata,
            )
            with pytest.raises(AnalysisError):
                forged.to_pandas()
            path = (
                tmp_path
                / data.rows.receipt.project_relative_path
                / data.rows.receipt.file_manifest[0].relative_path
            )
            original = path.read_bytes()
            try:
                table = pq.read_table(path)
                value = data.schema.values[0].slot_id
                table = table.set_column(
                    table.schema.get_field_index(value),
                    value,
                    pa.array([0] * len(table), type=pa.int64()),
                )
                pq.write_table(table, path)
                corrupted = path.read_bytes()
                with pytest.raises(AnalysisError):
                    session.persisted_frame(saved_groups.artifact_id).to_pandas()
                assert path.read_bytes() == corrupted
            finally:
                path.write_bytes(original)
        classifications = session.read(members, {"category": category})
        chosen = classifications.where(classifications["category"] == "absent")
        empty = session.population_from(
            chosen, on=((chosen.axes[0], session.catalog.get("entity:counts.events")),)
        )
        for query in (
            session.observe({"value": metric}, by=(window,), where=mv.in_population(empty)),
            session.observe({"value": metric}, by=(window,), where=mv.in_population(empty)),
        ):
            assert query.persist().to_pandas()["value"].tolist() == [0]
        calendar = session.time_scope(start="2026-09-01", end="2026-09-03").by(mv.grain("day"))
        timed = session.observe({"value": metric}, by=(calendar,))
        assert timed.persist().to_pandas()["value"].tolist() == [9, 1]
        assert timed.reduce({"value": timed["value"].sum()}).persist().to_pandas()[
            "value"
        ].tolist() == [row_count]
        if key_type == "int64" and profile == "table":
            saved_members = individual.persist()
            assert len(saved_members.to_pandas()) == row_count
            assert saved_members.to_pandas()["value"].tolist() == [1] * 9 + [0]
            _cold_outputs(
                tmp_path,
                session.id,
                first_day_count,
                (saved_members.artifact_id, saved_groups.artifact_id, result.artifact_id),
            )
        assert session._runtime.store.resources(session.id) == ()
