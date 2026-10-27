"""One native fixture binds additive and component allocation to recovery."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.numeric.attribution_workflows import allocation, basis
from tests.analysis.statistics.pandas_workflows import plain
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import catalog_value
from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace

pytestmark = pytest.mark.release


@pytest.mark.slow
def test_native_duckdb_attribution_independent_fixed_and_cold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    data = reference_data(backend)
    prefix = "TIMESTAMP " if backend == "trino" else ""
    data = replace(
        data,
        values=data.values
        + f",(9007199254740994,1,'b',3,{prefix}'2026-07-31 00:00:00'),(9007199254740995,1,'c',5,{prefix}'2026-07-31 00:00:00')",
        rows=data.rows
        + [
            {
                "id": identity,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 7, 31, tzinfo=timezone.utc),
            }
            for identity, tenant, amount in ((9007199254740994, "b", 3), (9007199254740995, "c", 5))
        ],
    )

    def forbid_snapshot(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Ordinary C09 reads must not claim a snapshot capture")

    monkeypatch.setattr("marivo.datasource.domain_snapshot.capture", forbid_snapshot)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        ms.load(workspace_dir=tmp_path)
        session = mv.session.get_or_create("r94-attribution", timezone="UTC")
        members = session.members(session.catalog.get("entity:sales.facts").value)
        bucket = catalog_value(session.catalog, "sales.facts.bucket", kind="field")
        happened = catalog_value(session.catalog, "sales.facts.happened", kind="field")

        def endpoint(month: int) -> mv.AnalysisFrame:
            return session.observe(
                {"value": session.catalog.get("metric:sales.total").value},
                by=mv.product(
                    session.time_scope(
                        start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"
                    ),
                    mv.coordinates(by=(bucket,), keys=(("a",), ("b",), ("c",))),
                ),
            )

        def facts(month: int) -> mv.AnalysisFrame:
            values = session.read(
                members,
                {
                    "channel": bucket,
                    "amount": catalog_value(session.catalog, "sales.facts.amount", kind="field"),
                    "time": happened,
                },
            )
            return values.where(
                (values["time"] >= datetime(2026, month, 1, tzinfo=timezone.utc))
                & (values["time"] < datetime(2026, month + 1, 1, tzinfo=timezone.utc))
            )

        current, baseline = endpoint(8), endpoint(7)
        outputs = {
            "groups": current.compare(
                {
                    "reference": mv.baseline(
                        baseline,
                        on=tuple(zip(current.axes, baseline.axes, strict=True)),
                        match=mv.ordinal(),
                    )
                }
            ).persist(),
            "difference": current.reduce({"value": current["value"].sum()})
            .compare(
                {"reference": mv.baseline(baseline.reduce({"value": baseline["value"].sum()}))}
            )
            .persist(),
            "current": facts(8).persist(),
            "baseline": facts(7).persist(),
        }
        grouped = plain(outputs["groups"])
        assert dict(zip(grouped.key_1, grouped["reference.absolute.value"], strict=True)) == {
            "a": 2,
            "b": 1,
            "c": -5,
        }
        assert plain(outputs["difference"])["reference.absolute.value"].tolist() == [-2]
        allocated = allocation(basis(outputs["current"]), basis(outputs["baseline"]), parents=())
        assert dict(zip(allocated.channel, allocated.contribution, strict=True)) == {
            "a": 2,
            "b": 1,
            "c": -5,
        }
        source_trace.save(
            "attribution-source-duckdb-sum-single",
            {**case.environment, "profile": profile},
            {
                "columns": data.columns,
                "facts": data.values,
                "identity_types": ["string", "int64", "int64"],
            },
            {"target": -2, "current": [2, 4, 0], "baseline": [0, 3, 5], "contribution": [2, 1, -5]},
            outputs["groups"],
            (case.session,),
        )
        state: dict[str, Json] = {
            "session": session.id,
            "outputs": {
                name: {"artifact": value.artifact_id, "snapshot": snapshot(value)}
                for name, value in outputs.items()
            },
        }
        (tmp_path / "r94-attribution.json").write_bytes(encode(state))
    shutil.rmtree(tmp_path / "models")
    for pattern in ("source.duckdb*",):
        for path in tmp_path.glob(pattern):
            path.unlink()
    repository = PROJECT_ROOT
    reports: list[Json] = []
    for phase in ("offline", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.materialization.attribution_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "attribution-recovery-" + backend + ".json").write_bytes(
            encode(
                {"backend": backend, "source_pid": os.getpid(), "source": state, "reports": reports}
            )
        )
