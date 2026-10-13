"""Native Count reduction, flat grouped state and source-free recovery witnesses."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.graph_protocol import validate_metadata
from marivo.analysis.materialization.graph_storage import read_result
from marivo.analysis.methods.coordinate_state import KeyedCoordinateLayout
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case
from tests.support.execution_logs import execution_records
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


def _stage_rows(root: Path, offset: int) -> list[int]:
    rows: list[int] = []
    for record in execution_records(root)[offset:]:
        if (
            record.get("event") == "query.completed"
            and record.get("purpose") == "analysis.graph.stage"
        ):
            count = record["consumed_rows"]
            assert isinstance(count, int)
            rows.append(count)
    return rows


def _cold_rollup(
    root: Path, session_id: str, expected_count: int, artifacts: tuple[str, ...]
) -> None:
    script = """
import sys
from unittest.mock import patch
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('retained Count accessed Semantic or a source')

with patch.object(ms, 'load', forbidden), patch.object(DatasourceConnectionService, 'use_backend', forbidden):
    session = mv.session.resume(sys.argv[1], by='id')
    expected_count = int(sys.argv[2])
    for reference in sys.argv[3:]:
        value = session.artifact(reference)
        assert isinstance(value, mv.MaterializedNumericRelation)
        assert any(action.call == 'relation.rollup()' for action in value.contract().actions)
        assert value.rollup().execute().to_pandas()['value'].tolist() == [expected_count]
"""
    outcome = subprocess.run(
        [sys.executable, "-c", script, session_id, str(expected_count), *artifacts],
        cwd=root,
        env={**os.environ, "MARIVO_PROJECT_ROOT": str(root), "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert outcome.returncode == 0, outcome.stderr


@pytest.mark.parametrize(
    ("backend", "profile", "key_type"),
    [("duckdb", "table", "int64"), ("duckdb", "view", "string")],
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
        backend,
        profile,
        tmp_path,
        monkeypatch,
        _data(backend, key_type, row_count=row_count),
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
                + "category=ms.dimension_column(name='category',entity=events,column='category')\n"
                + "happened=ms.time_dimension_column(name='happened',entity=events,column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
                + "event_count=ms.count(name='event_count',entity=events,time=happened)\n",
            }
        )
        session = mv.session.get_or_create("native-count", report_timezone="UTC")
        entity = ms.ref.entity("counts.events")
        metric = ms.ref.metric("counts.event_count")
        category = ms.ref.dimension("counts.events.category")
        members = session.members(entity)
        window = mv.time_scope(start="2026-09-01", end="2026-09-02")
        scalar = members.observe(metric, during=window)
        individual = members.observe(metric, during=window, by=(mv.member(),))
        grouped = members.observe(metric, during=window, by=(category,))
        assert isinstance(scalar, mv.LogicalNumericRelation)
        assert isinstance(individual, mv.LogicalNumericRelation)
        assert isinstance(grouped, mv.LogicalNumericRelation)
        # The integer-key case covers this member rollup after restart below;
        # keep a source-execution witness for the string-key view profile.
        reductions: tuple[
            mv.LogicalNumericRelation
            | mv.LogicalRolledNumericRelation
            | mv.LogicalStatisticRelation,
            ...,
        ] = (scalar,) if key_type == "int64" else (scalar, individual.rollup())
        for logical in reductions:
            offset = len(execution_records(tmp_path))
            result = logical.execute()
            assert result.to_pandas()["value"].tolist() == [first_day_count]
            assert _stage_rows(tmp_path, offset) == [1]
            assert result._dataset is not None
            assert all(len(part.table) == 1 for part in result._dataset.verified().parts)
        offset = len(execution_records(tmp_path))
        saved_groups = grouped.execute()
        frame = saved_groups.to_pandas()
        assert frame.set_index("group")["value"].to_dict() == {
            "even": first_day_count // 2,
            "odd": (first_day_count + 1) // 2,
        }
        assert _stage_rows(tmp_path, offset) == [2]
        assert saved_groups._dataset is not None
        coordinate = next(
            p for p in saved_groups._dataset.verified().parts if p.role == "coordinate_state"
        )
        assert list(coordinate.table.columns) == ["key_0", "coordinate_state__present"]
        if backend == "duckdb" and key_type == "int64":
            descriptor = saved_groups._dataset.artifact.descriptor
            receipt = next(p for p in descriptor.parts if p.role == "coordinate_state")
            assert isinstance(receipt.layout, KeyedCoordinateLayout)
            changed = replace(receipt, layout=replace(receipt.layout, owner="coverage"))
            with pytest.raises(IntegrityError, match="owning receipt differs"):
                validate_metadata(
                    replace(
                        descriptor,
                        parts=tuple(changed if p is receipt else p for p in descriptor.parts),
                    )
                )
            path = tmp_path / receipt.local.project_relative_path / "data.parquet"
            original = path.read_bytes()
            try:
                table = pq.read_table(path)
                table = table.set_column(
                    1, "coordinate_state__present", pa.array([False] * len(table))
                )
                pq.write_table(table, path)
                corrupted = path.read_bytes()
                with pytest.raises(IntegrityError, match="immutable receipt digest"):
                    read_result(tmp_path, descriptor)
                assert path.read_bytes() == corrupted
            finally:
                path.write_bytes(original)
        offset = len(execution_records(tmp_path))
        assert grouped.rollup().execute().to_pandas()["value"].tolist() == [first_day_count]
        assert _stage_rows(tmp_path, offset) == [2]
        classification = members.read(category)
        assert isinstance(classification, mv.LogicalCategoryRelation)
        empty = classification.where(classification.value.eq("absent")).members()
        assert isinstance(empty, mv.LogicalAnalysisDomain)
        for empty_logical in (
            empty.observe(metric, during=window),
            empty.observe(metric, during=window, by=(mv.member(),)).rollup(),
        ):
            assert empty_logical.execute().to_pandas()["value"].tolist() == [0]
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-09-01", end="2026-09-03"), grain=mv.grain("day")
        )
        timed = members.observe(metric, during=grid)
        assert isinstance(timed, mv.LogicalNumericRelation)
        assert timed._node.root.signature.domain.kind == "group"
        offset = len(execution_records(tmp_path))
        assert timed.rollup().execute().to_pandas()["value"].tolist() == [row_count]
        assert _stage_rows(tmp_path, offset) == [1]
        if key_type == "int64" and profile == "table":
            assert saved_groups is not None
            saved_members = individual.execute()
            _cold_rollup(
                tmp_path,
                session.id,
                first_day_count,
                (saved_members.state.artifact_ref.ref, saved_groups.state.artifact_ref.ref),
            )
