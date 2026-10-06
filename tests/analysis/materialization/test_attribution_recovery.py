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
from marivo.refs import DimensionKind, Ref
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import source_case
from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_attribution_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    data = reference_data(backend)
    data = replace(
        data,
        columns=data.columns + ", region VARCHAR(10)",
        values=data.values.replace("00:00:00')", "00:00:00','east')"),
        clickhouse_columns=data.clickhouse_columns + ", region String",
        rows=[{**row, "region": "east"} for row in data.rows],
    )
    prefix = "TIMESTAMP " if backend == "trino" else ""
    data = replace(
        data,
        values=data.values
        + f",(9007199254740994,1,'b',3,{prefix}'2026-07-31 00:00:00','east'),(9007199254740995,1,'c',5,{prefix}'2026-07-31 00:00:00','east')",
        rows=data.rows
        + [
            {
                "id": identity,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 7, 31, tzinfo=timezone.utc),
                "region": "east",
            }
            for identity, tenant, amount in ((9007199254740994, "b", 3), (9007199254740995, "c", 5))
        ],
    )

    def forbid_snapshot(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Ordinary C09 reads must not claim a snapshot capture")

    monkeypatch.setattr("marivo.datasource.domain_snapshot.capture", forbid_snapshot)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        models = tmp_path / "models/semantic/sales/models.py"
        models.write_text(
            models.read_text()
            + "\nregion = ms.dimension_column(name='region', entity=facts, column='region')\n"
        )
        ms.load(workspace_dir=tmp_path)
        session = mv.session.get_or_create("r94-attribution", report_timezone="UTC")
        originals: dict[str, Json] = {}
        variants: list[Json] = []
        for metric_kind in ("sum", "mean"):
            for axis_kind in ("single", "joint") if backend == "duckdb" else ("single",):
                axes: tuple[Ref[DimensionKind], ...] = (ms.ref.dimension("sales.facts.bucket"),)
                if axis_kind == "joint":
                    axes += (ms.ref.dimension("sales.facts.region"),)
                members = session.members(ms.ref.entity("sales.facts"))
                current = members.observe(
                    ms.ref.metric("sales.total" if metric_kind == "sum" else "sales.average"),
                    during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                    coordinates=axes,
                ).rollup()
                baseline = members.observe(
                    ms.ref.metric("sales.total" if metric_kind == "sum" else "sales.average"),
                    during=mv.time_scope(start="2026-07-31", end="2026-08-01"),
                    coordinates=axes,
                ).rollup()
                change = current.compare(baseline)
                difference = change.execute()
                allocation = change.attribute(axes=axes).execute()
                assert isinstance(difference, mv.MaterializedDifferenceRelation)
                assert difference.to_pandas().value.tolist() == (
                    [-2] if metric_kind == "sum" else [-1]
                )
                expected = (
                    {"a": 2, "b": 1, "c": -5}
                    if metric_kind == "sum"
                    else {"a": 1, "b": 0.5, "c": -2.5}
                )
                frame = allocation.contribution.to_pandas()
                assert dict(zip(frame.coord_0, frame.value, strict=True)) == expected
                name = metric_kind + ":" + axis_kind
                originals[name + ":change"] = snapshot(difference)
                originals[name + ":allocation"] = snapshot(allocation)
                variants.append({"name": name, "metric_kind": metric_kind, "axis_kind": axis_kind})
                source_trace.save(
                    "attribution-source-" + backend + "-" + metric_kind + "-" + axis_kind,
                    {**case.environment, "profile": profile},
                    {
                        "columns": data.columns,
                        "facts": data.values,
                        "identity_types": ["string", "int64", "int64"],
                    },
                    {
                        "metric_kind": metric_kind,
                        "axis_kind": axis_kind,
                        "target": -2 if metric_kind == "sum" else -1,
                        "current": [2, 4, 0] if metric_kind == "sum" else [1, 2, 0],
                        "baseline": [0, 3, 5] if metric_kind == "sum" else [0, 1.5, 2.5],
                        "contribution": [2, 1, -5] if metric_kind == "sum" else [1, 0.5, -2.5],
                    },
                    allocation,
                    (case.session,),
                )
        state: dict[str, Json] = {
            "session": session.id,
            "originals": originals,
            "variants": variants,
        }
        (tmp_path / "r94-attribution.json").write_bytes(encode(state))
    shutil.rmtree(tmp_path / "models")
    for pattern in ("source.duckdb*", "source.sqlite*"):
        for path in tmp_path.glob(pattern):
            path.unlink()
    repository = PROJECT_ROOT
    reports: list[Json] = []
    for phase in ("fixed", "cold"):
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
