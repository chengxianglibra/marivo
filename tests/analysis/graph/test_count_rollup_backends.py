"""Native Count reduction, nested grouped state and source-free recovery witnesses."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case
from tests.support.execution_logs import execution_records
from tests.support.paths import PROJECT_ROOT


def _data(backend: str, key_type: Literal["int64", "string"]) -> SourceData:
    entries: list[dict[str, object]] = []
    values: list[str] = []
    for index in range(1, 1001):
        identity = str(index) if key_type == "string" else index
        category = "even" if index % 2 == 0 else "odd"
        point = datetime(2026, 9, 2 if index == 1000 else 1, tzinfo=timezone.utc)
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


def _cold_rollup(root: Path, session_id: str, artifacts: tuple[str, ...]) -> None:
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
    for reference in sys.argv[2:]:
        value = session.artifact(reference)
        assert isinstance(value, mv.MaterializedNumericRelation)
        assert any(action.call == 'relation.rollup()' for action in value.contract().actions)
        assert value.rollup().execute().to_pandas()['value'].tolist() == [999]
"""
    outcome = subprocess.run(
        [sys.executable, "-c", script, session_id, *artifacts],
        cwd=root,
        env={**os.environ, "MARIVO_PROJECT_ROOT": str(root), "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert outcome.returncode == 0, outcome.stderr


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend,profile",
    [
        ("postgres", "table"),
        ("mysql", "innodb-table"),
        ("trino", "iceberg"),
        ("trino", "non-iceberg"),
        ("clickhouse", "mergetree"),
    ],
)
@pytest.mark.parametrize("key_type", ["int64", "string"])
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
    with source_case(backend, profile, tmp_path, monkeypatch, _data(backend, key_type)) as case:
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
        individual = members.observe(metric, during=window, by=(entity,))
        grouped = members.observe(metric, during=window, by=(category,))
        assert isinstance(scalar, mv.LogicalNumericRelation)
        assert isinstance(individual, mv.LogicalNumericRelation)
        assert isinstance(grouped, mv.LogicalNumericRelation)
        reductions: tuple[
            mv.LogicalNumericRelation
            | mv.LogicalRolledNumericRelation
            | mv.LogicalStatisticRelation,
            ...,
        ] = (
            scalar,
            individual.rollup(),
            scalar.rollup(),
            individual.summarize(mv.sum()),
        )
        if backend == "trino":
            reductions += (grouped.rollup(),)
        for logical in reductions:
            offset = len(execution_records(tmp_path))
            result = logical.execute()
            assert result.to_pandas()["value"].tolist() == [999]
            assert _stage_rows(tmp_path, offset) == [1]
            assert result._dataset is not None
            assert all(part.table.num_rows == 1 for part in result._dataset.verified().parts)
        saved_groups = None
        if backend in ("postgres", "mysql", "clickhouse"):
            for unsupported in (grouped, grouped.rollup()):
                offset = len(execution_records(tmp_path))
                with pytest.raises(MethodRegistrationError, match="nested contribution-coordinate"):
                    unsupported.execute()
                assert not any(
                    record.get("event") == "query.submitted"
                    and str(record.get("purpose", "")).startswith("analysis.graph.")
                    for record in execution_records(tmp_path)[offset:]
                )
        elif backend == "trino":
            offset = len(execution_records(tmp_path))
            saved_groups = grouped.execute()
            frame = saved_groups.to_pandas()
            assert frame.set_index("group")["value"].to_dict() == {"even": 499, "odd": 500}
            assert _stage_rows(tmp_path, offset) == [2]
            assert saved_groups._dataset is not None
            assert {part.role for part in saved_groups._dataset.verified().parts} >= {
                "coordinate_state",
                "original_state",
                "coverage",
            }
        classification = members.read(category)
        assert isinstance(classification, mv.LogicalCategoryRelation)
        empty = classification.where(classification.value.eq("absent")).members()
        assert isinstance(empty, mv.LogicalAnalysisDomain)
        for empty_logical in (
            empty.observe(metric, during=window),
            empty.observe(metric, during=window, by=(entity,)).rollup(),
        ):
            assert empty_logical.execute().to_pandas()["value"].tolist() == [0]
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-09-01", end="2026-09-03"), grain=mv.grain("day")
        )
        timed = members.observe(metric, during=grid)
        assert isinstance(timed, mv.LogicalNumericRelation)
        assert timed._node.root.signature.domain.kind == "group"
        offset = len(execution_records(tmp_path))
        assert timed.rollup().execute().to_pandas()["value"].tolist() == [1000]
        assert _stage_rows(tmp_path, offset) == [1]
        if backend == "trino" and profile == "non-iceberg" and key_type == "int64":
            assert saved_groups is not None
            saved_members = individual.execute()
            _cold_rollup(
                tmp_path,
                session.id,
                (saved_members.state.artifact_ref.ref, saved_groups.state.artifact_ref.ref),
            )
