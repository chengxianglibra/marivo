"""Native C08 reference, rank and opportunity producers bind offline recovery."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import source_case
from tests.support.json import Json, checked, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_reference_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, reference_data(backend)) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-reference", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))

        values = members.observe(ms.ref.metric("sales.total"))
        bucket = members.read(ms.ref.dimension("sales.facts.bucket"))
        assert isinstance(values, mv.LogicalNumericRelation)
        assert isinstance(bucket, mv.LogicalCategoryRelation)
        groups = values.group_by(bucket).rollup()
        weights = groups.share_of(groups.rollup())
        reference = mv.reference_weights(
            weights, strata=(bucket,), unit=ms.ref.entity("sales.facts")
        )
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-08-01", end="2026-08-03"), grain=mv.grain("day")
        )
        opportunities = members.each(grid).observe(ms.ref.metric("sales.total"), during=grid.window)
        assert isinstance(opportunities, mv.LogicalNumericRelation)
        selected = bucket.where(bucket.value.eq("a")).members()
        empty = bucket.where(bucket.value.eq("absent")).members()
        originals: dict[str, Json] = {}
        logicals = (
            ("current", values),
            ("members", members),
            ("bucket", bucket),
            ("reference", values.rollup()),
            ("groups", groups),
            ("weights", weights),
            ("opportunities", opportunities),
            ("share", values.share_of(values.rollup())),
            ("ranking", values.rank(order="descending", ties="dense")),
            ("cohort", members.cohort(opportunities.value.gt(0), rule=mv.any_instance())),
            ("cohort_two", members.cohort(opportunities.value.gt(0), rule=mv.at_least(2))),
            ("penetration", selected.penetration_in(members)),
            ("empty_penetration", empty.penetration_in(empty)),
            ("standardized", groups.standardize(reference=reference)),
        )
        for name, logical in logicals:
            result = logical.execute()
            originals[name] = snapshot(result)
            if isinstance(result, mv.MaterializedRankingResult):
                source_trace.record(result.ranks, definition=result._node.definition)
            elif isinstance(result, mv.MaterializedCategoryRelation):
                source_trace.record(result)
            else:
                source_trace.record(result)
        state: dict[str, Json] = {"session": session.id, "originals": originals}
        (tmp_path / "r94-reference.json").write_bytes(encode(state))
        source_trace.save(
            "reference-source-" + backend,
            {**case.environment, "profile": profile},
            {"rows": 3, "opportunity_rows": 6},
            {
                "keys": [
                    ["a", 9007199254740992, 1],
                    ["a", 9007199254740993, 2],
                    ["b", 9007199254740993, 1],
                ],
                "share": [1 / 3, 0, 2 / 3],
                "ranks": [2, 3, 1],
                "cohort": [0, 2],
                "cohort_two": [],
                "penetration": 2 / 3,
                "standardized": 2 * (1 / 3) + 4 * (2 / 3),
            },
            None,
            (case.session,),
        )
        environment = checked({**case.environment, "profile": profile})
    shutil.rmtree(tmp_path / "models")
    for pattern in ("source.duckdb*", "source.sqlite*"):
        for path in tmp_path.glob(pattern):
            path.unlink()
    reports: list[Json] = []
    for phase in ("fixed", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.materialization.reference_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=PROJECT_ROOT,
            env=dict(
                os.environ,
                PYTHONPATH=str(PROJECT_ROOT),
                MARIVO_TELEMETRY="off",
            ),
            capture_output=True,
            text=True,
            timeout=360,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "reference-recovery-" + backend + ".json").write_bytes(
            encode(
                {
                    "environment": environment,
                    "source_pid": os.getpid(),
                    "source": state,
                    "reports": reports,
                }
            )
        )
