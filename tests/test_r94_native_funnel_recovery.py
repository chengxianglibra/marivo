"""Real remote Funnel producers with canonical offline and cold recovery."""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import ibis
import pytest

from marivo.datasource.ir import TableSourceIR
from scripts.r9_qualification_requirements import Json, encode, read
from tests.r9_source_cases import SourceData, source_case
from tests.r93_source_trace import SourceTrace
from tests.r94_funnel_public_recovery_worker import author, materialize


def test_remote_funnel_declarations_are_bounded() -> None:
    from marivo.analysis.methods.funnel_physical import implementations
    from marivo.analysis.methods.physical import ScalarType, SourceShape, TimeShape
    from marivo.analysis.methods.semantics import MethodKey

    for method in (
        "funnel.entry_axes",
        "funnel.reduce",
        "funnel.compare",
        "funnel.read",
        "funnel_ratio_mix",
    ):
        remote = [
            item
            for item in implementations(MethodKey(method))
            if isinstance(item.key.shape, SourceShape)
            and item.key.shape.backend in ("postgres", "mysql", "trino", "clickhouse")
        ]
        if method not in ("funnel.entry_axes", "funnel.reduce"):
            assert remote == []
            continue
        assert len(remote) == 4
        for item in remote:
            assert isinstance(item.key.shape, SourceShape)
            assert item.key.shape == SourceShape(
                item.key.shape.backend, "table", "native", TimeShape("instant", "us", "UTC")
            )
            assert item.key.input_types == (ScalarType("int64"),) * (
                1 if method == "funnel.entry_axes" else 2
            )
            assert item.key.route == ("ibis" if method == "funnel.entry_axes" else "ibis_python")


def source_data(rows: list[dict[str, object]], backend: str, events: bool) -> SourceData:
    def literal(value: object) -> str:
        if isinstance(value, datetime):
            timestamp = value.strftime("%Y-%m-%d %H:%M:%S.%f")
            return f"TIMESTAMP '{timestamp} UTC'" if backend == "trino" else f"'{timestamp}'"
        return repr(value) if isinstance(value, str) else str(value)

    temporal = {"postgres": "TIMESTAMPTZ", "trino": "TIMESTAMP(6) WITH TIME ZONE"}.get(
        backend, "TIMESTAMP(6)"
    )
    return SourceData(
        f"oid BIGINT, sid BIGINT, kind VARCHAR(20), instant {temporal}" if events else "sid BIGINT",
        ",".join("(" + ",".join(literal(value) for value in row.values()) + ")" for row in rows),
        "oid Int64, sid Int64, kind String, instant DateTime64(6, 'UTC')"
        if events
        else "sid Int64",
        rows,
    )


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_native_funnel_nonempty_findings_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, r93_source_trace: SourceTrace
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    author(tmp_path, "table")
    local = ibis.duckdb.connect(tmp_path / "source.duckdb")
    try:
        people = local.table("subjects").to_pyarrow().to_pylist()
        facts = local.table("facts").to_pyarrow().to_pylist()
    finally:
        local.disconnect()
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with (
        source_case(
            backend, profile, tmp_path, monkeypatch, source_data(people, backend, False)
        ) as subjects,
        source_case(
            backend, profile, tmp_path, monkeypatch, source_data(facts, backend, True)
        ) as events,
    ):
        assert isinstance(subjects.source, TableSourceIR) and isinstance(
            events.source, TableSourceIR
        )
        arguments = {
            **subjects.session.datasource.fields,
            **{
                name + "_env": value for name, value in subjects.session.datasource.env_refs.items()
            },
        }
        user = arguments.pop("user", None)
        if user is not None:
            monkeypatch.setenv("MARIVO_R94_FUNNEL_READER", str(user))
            arguments["user_env"] = "MARIVO_R94_FUNNEL_READER"
        (tmp_path / "models/datasources/warehouse.py").write_text(
            "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse',"
            + ",".join(f"{name}={value!r}" for name, value in arguments.items())
            + ")\n"
        )
        model = tmp_path / "models/semantic/commerce/objects.py"
        code = model.read_text()
        for logical, case in (("subjects", subjects), ("facts", events)):
            assert isinstance(case.source, TableSourceIR)
            code = code.replace(
                f"md.table({logical!r})",
                f"md.table({case.source.table!r},database={case.source.database!r})",
            )
        model.write_text(code)
        # Remove the scaffold source before native production, not just before recovery.
        for path in tmp_path.glob("source.duckdb*"):
            path.unlink()
        produced = materialize(tmp_path)
        r93_source_trace.save(
            "native-funnel-source-" + backend,
            {**subjects.environment, "profile": profile},
            {
                "subjects": people,
                "facts": [
                    {
                        name: str(value) if isinstance(value, datetime) else value
                        for name, value in row.items()
                    }
                    for row in facts
                ],
            },
            {"comparison_loss_deltas": [-1, 1], "comparison_findings": 2},
            None,
            (subjects.session, events.session),
        )
    shutil.rmtree(tmp_path / "models")
    reports: list[Json] = [produced]
    repository = Path(__file__).resolve().parents[1]
    for phase in ("fixed", "cold"):
        report = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_funnel_public_recovery_worker",
                str(tmp_path),
                phase,
                str(report),
                "table",
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(report))
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "native-funnel-" + backend + ".json").write_bytes(
            encode(
                {
                    "backend": backend,
                    "profile": profile,
                    "reports": reports,
                    "manifest": read(tmp_path / "r94-funnel.json"),
                }
            )
        )
