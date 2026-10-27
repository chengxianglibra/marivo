"""Native C08 reference, rank and opportunity producers bind offline recovery."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.materialization.reference_recovery_worker import recipes
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import catalog_value, terminal_runs
from tests.support.json import Json, checked, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.slow
@pytest.mark.release
def test_native_reference_independent_fixed_and_cold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, reference_data(backend)) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-reference", timezone="UTC")
        members = session.members(session.catalog.get("entity:sales.facts").value)

        bucket = catalog_value(session.catalog, "sales.facts.bucket", kind="field")
        values = session.observe(
            {"value": session.catalog.get("metric:sales.total").value}, by=members
        )
        groups = session.observe(
            {"value": session.catalog.get("metric:sales.total").value}, by=(bucket,)
        )
        calendar = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
        opportunities = session.observe(
            {"value": session.catalog.get("metric:sales.total").value},
            by=mv.product(calendar, members),
        )
        category = session.read(members, {"bucket": bucket})
        chosen = values.where(values["value"] > 0)
        selected = session.population_from(
            chosen, on=((chosen.axes[0], session.catalog.get("entity:sales.facts").value),)
        )
        total = session.observe(
            {"value": session.catalog.get("metric:sales.total").value}
        ).compute()
        denominator = total.to_pandas()["value"].iloc[0]
        assert denominator == 6
        queries = {
            "current": values,
            "bucket": category,
            "basis": session.read(
                members,
                {
                    "amount": catalog_value(session.catalog, "sales.facts.amount", kind="field"),
                    "bucket": bucket,
                },
            ),
            "groups": groups,
            "weights": groups.select({"share": groups["value"] / denominator}),
            "opportunities": opportunities,
            "share": values.select({"share": values["value"] / denominator}),
            "selected": session.read(selected, {"bucket": bucket}),
            "empty": category.where(category["bucket"] == "absent"),
        }
        outputs = {name: query.persist() for name, query in queries.items()}
        derived = recipes(outputs)
        assert derived["ranks"] == [2, None, 1]
        assert derived["opportunity_count"] == 6
        state: dict[str, Json] = {
            "session": session.id,
            "outputs": {
                name: {"artifact": value.artifact_id, "snapshot": snapshot(value)}
                for name, value in outputs.items()
            },
            "derived": derived,
        }
        (tmp_path / "r94-reference.json").write_bytes(encode(state))
        for value in outputs.values():
            source_trace.record(value)
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
                "ranks": [2, None, 1],
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
    for pattern in ("source.duckdb*",):
        for path in tmp_path.glob(pattern):
            path.unlink()
    reports: list[Json] = []
    for phase in ("cold",):
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
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 2
    assert all(obj(report)["derived"] == derived for report in reports)
    assert all(obj(report)["run_count"] == len(terminal_runs(session)) for report in reports)
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
