"""Native nested, union and ratio producers bind separate fixed/cold processes."""

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
from tests.json_support import Json, checked, encode, obj, read
from tests.r9_source_cases import source_case
from tests.r93_source_trace import SourceTrace
from tests.r94_domain_recovery_worker import snapshot
from tests.test_r93_capability_consumers import _author_c05_project
from tests.test_r93_reference_consumers import _data


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_comparison_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, _data(backend)) as case:
        _author_c05_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-comparison", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))

        def observe(month: int) -> mv.LogicalNumericRelation:
            value = members.observe(
                ms.ref.metric("sales.total"),
                during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            )
            assert isinstance(value, mv.LogicalNumericRelation)
            return value

        current, baseline, earlier = observe(8), observe(7), observe(6)
        first, second = current.compare(baseline), baseline.compare(earlier)
        left, right = current.where(current.value.gt(0)), baseline.where(baseline.value.gt(0))
        design = mv.TimeChange(pairing=mv.UnionKeys(missing="keep"))
        originals: dict[str, Json] = {}
        for name, logical in (
            ("current", current),
            ("first", first),
            ("second", second),
            ("left", left),
            ("right", right),
            ("nested", first.compare(second)),
            ("union", left.compare(right, design=design)),
            ("ratio", current.ratio(current)),
        ):
            result = logical.execute()
            originals[name] = snapshot(result)
            r93_source_trace.record(result)
        state: dict[str, Json] = {"session": session.id, "originals": originals}
        (tmp_path / "r94-comparison.json").write_bytes(encode(state))
        r93_source_trace.save(
            "comparison-source-" + backend,
            {**case.environment, "profile": profile},
            {"rows": 3},
            {
                "keys": [
                    ["a", 9007199254740992, 1],
                    ["a", 9007199254740993, 2],
                    ["b", 9007199254740993, 1],
                ],
                "nested": [2, 0, 4],
                "ratio": [1, None, 1],
                "union": [None, None],
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
                "tests.r94_comparison_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(
                os.environ,
                PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                MARIVO_TELEMETRY="off",
            ),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "comparison-recovery-" + backend + ".json").write_bytes(
            encode(
                {
                    "environment": environment,
                    "source_pid": os.getpid(),
                    "source": state,
                    "reports": reports,
                }
            )
        )
