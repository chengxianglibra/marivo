"""One native versioned fixture binds both clocks and four attribute families."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.datasource.source_cases import SourceData, source_case
from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_versioned_members_independent_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (9007199254740992, "a", "2026-08-01", "2026-09-01", 10),
        (9007199254740993, "a", "2026-08-01", None, 20),
        (9007199254740993, "b", "2026-08-01", None, 30),
        (9007199254740992, "a", "2026-09-01", None, 40),
    ]
    prefix = "DATE " if backend == "trino" else ""
    data = SourceData(
        "id BIGINT, tenant VARCHAR(10), beginning DATE, ending DATE, amount BIGINT, enabled BOOLEAN",
        ",".join(
            f"({identity},'{tenant}',{prefix}'{start}',"
            + ("NULL" if end is None else f"{prefix}'{end}'")
            + f",{amount},{str(amount > 15).upper()})"
            for identity, tenant, start, end, amount in facts
        ),
        "id Int64, tenant String, beginning Date, ending Nullable(Date), amount Int64, enabled Bool",
        [
            {
                "id": identity,
                "tenant": tenant,
                "beginning": date.fromisoformat(start),
                "ending": None if end is None else date.fromisoformat(end),
                "amount": amount,
                "enabled": amount > 15,
            }
            for identity, tenant, start, end, amount in facts
        ],
    )
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_R93_READER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_R93_READER"
        models = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for kind in ("snapshot", "validity"):
            name = "subjects_" + kind
            version = (
                f"ms.snapshot(partition_field=ms.ref.time_dimension('sales.{name}.beginning'),grain='day',timezone='UTC')"
                if kind == "snapshot"
                else f"ms.validity(valid_from=ms.ref.time_dimension('sales.{name}.beginning'),valid_to=ms.ref.time_dimension('sales.{name}.ending'),interval='closed_open',open_end=(None,),timezone='UTC')"
            )
            models += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id','tenant'],versioning={version})\n"
            for field in ("id", "tenant", "enabled"):
                models += f"{name}_{field}=ms.dimension_column(name={field!r},entity={name},column={field!r})\n"
            for field in ("beginning", "ending"):
                models += f"{name}_{field}=ms.time_dimension_column(name={field!r},entity={name},column={field!r},granularity='day')\n"
            models += f"{name}_amount=ms.measure_column(name='amount',entity={name},column='amount',additivity=ms.additive_all())\n"
            if backend == "mysql":
                models += f"@ms.dimension(entity={name})\ndef enabled_{kind}({name}):\n    return {name}.enabled == 1\n"
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                "sales/models.py": models,
            }
        )
        session = mv.session.get_or_create("r94-versions", report_timezone="UTC")
        august = datetime(2026, 8, 1, tzinfo=timezone.utc)
        september = datetime(2026, 9, 1, tzinfo=timezone.utc)
        originals: dict[str, Json] = {}
        for kind in ("snapshot", "validity"):
            entity = ms.ref.entity("sales.subjects_" + kind)
            with monkeypatch.context() as construction:
                construction.setattr(SourceSession, "batches", forbidden)
                construction.setattr(SourceSession, "compile", forbidden)
                with pytest.raises(AnalysisError):
                    session.members(entity)
                current = session.members(entity, at=august)
                with pytest.raises(AnalysisError):
                    current.read(ms.ref.measure("sales.subjects_" + kind + ".amount"))
            for label, point in (
                ("august", august),
                ("september", september),
                (
                    "before_september",
                    mv.time_scope(start="2026-08-01", end="2026-09-01").before_end,
                ),
                (
                    "before_second_day",
                    mv.time_scope(start="2026-08-01", end="2026-08-02").before_end,
                ),
            ):
                result = session.members(entity, at=point).execute()
                frame = result.to_pandas()
                expected = (
                    []
                    if kind == "snapshot" and label == "before_september"
                    else [(9007199254740992, "a")]
                    if kind == "snapshot" and label == "september"
                    else [(9007199254740992, "a"), (9007199254740993, "a"), (9007199254740993, "b")]
                )
                assert list(zip(frame.member, frame.coord_0, strict=True)) == expected
                originals[kind + ":" + label] = snapshot(result)
                source_trace.record(result)
            relations = {
                "numeric": current.read(
                    ms.ref.measure("sales.subjects_" + kind + ".amount"), at=august
                ),
                "category": current.read(
                    ms.ref.dimension("sales.subjects_" + kind + ".tenant"), at=august
                ),
                "boolean": current.read(
                    ms.ref.dimension(
                        "sales.subjects_"
                        + kind
                        + (".enabled_" + kind if backend == "mysql" else ".enabled")
                    ),
                    at=august,
                ),
                "temporal": current.read(
                    ms.ref.time_dimension("sales.subjects_" + kind + ".beginning"), at=august
                ),
            }
            for label, relation in relations.items():
                attribute = relation.execute()
                assert (
                    attribute.to_pandas().value.tolist()
                    == {
                        "numeric": [10, 20, 30],
                        "category": ["a", "a", "b"],
                        "boolean": [False, True, True],
                        "temporal": [date(2026, 8, 1)] * 3,
                    }[label]
                )
                originals[kind + ":" + label] = snapshot(attribute)
                source_trace.record(attribute)
            if kind == "snapshot":
                grid = mv.time_grid(
                    during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
                    grain=mv.grain("day"),
                )
                product = current.each(grid).execute()
                assert len(product.to_pandas()) == 6
                originals["snapshot:product"] = snapshot(product)
                source_trace.record(product)
        state: dict[str, Json] = {"session": session.id, "originals": originals}
        (tmp_path / "r94-versions.json").write_bytes(encode(state))
        source_trace.save(
            "versions-source-" + backend,
            {**case.environment, "profile": profile},
            {"facts": data.values, "identity_types": ["int64", "string"]},
            {
                "amounts": [10, 20, 30],
                "snapshot_september_members": 1,
                "validity_september_members": 3,
                "snapshot_before_september_members": 0,
                "validity_before_september_members": 3,
                "product_cells": 6,
                "construction_business_reads_forbidden": True,
            },
            None,
            (case.session,),
        )
    shutil.rmtree(tmp_path / "models")
    for pattern in ("source.duckdb*", "source.sqlite*"):
        for path in tmp_path.glob(pattern):
            path.unlink()
    reports: list[Json] = []
    repository = PROJECT_ROOT
    for phase in ("fixed", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.materialization.versioned_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "versions-recovery-" + backend + ".json").write_bytes(
            encode(
                {"backend": backend, "source_pid": os.getpid(), "source": state, "reports": reports}
            )
        )
