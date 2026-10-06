"""One native composite fixture binds independent runtime roots to recovery."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json, encode, obj, read
from tests.r9_source_cases import Case, SourceData, datasource, source_case
from tests.r93_source_trace import SourceTrace
from tests.r94_domain_recovery_worker import forbidden, snapshot
from tests.r94_multiroot_recovery_worker import components


def data(backend: str, rows: tuple[tuple[int, str, int, int], ...]) -> SourceData:
    prefix = "TIMESTAMP " if backend == "trino" else ""
    return SourceData(
        "id BIGINT, owner VARCHAR(10), tenant BIGINT, amount BIGINT, happened TIMESTAMP",
        ",".join(
            f"({identity},'{owner}',{tenant},{amount},{prefix}'2026-08-01 00:00:00')"
            for identity, owner, tenant, amount in rows
        ),
        "id Int64, owner String, tenant Int64, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "owner": owner,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for identity, owner, tenant, amount in rows
        ],
    )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_independent_multiroot_fixed_and_cold(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    first, second = 9007199254740992, 9007199254740993
    datasets = (
        data(
            backend,
            ((1, "a", first, 0), (2, "a", second, 0), (3, "b", first, 0), (4, "c", first, 0)),
        ),
        data(
            backend,
            (
                (first, "a", first, 10),
                (second, "a", first, 20),
                (first + 2, "a", second, 50),
                (first + 3, "a", second, 40),
            ),
        ),
        data(
            backend,
            (
                (first, "a", first, 2),
                (second, "a", first, 3),
                (first + 2, "a", second, 7),
                (first + 3, "a", second, 13),
                (first + 4, "b", first, 4),
            ),
        ),
    )
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with ExitStack() as stack:
        if backend == "duckdb":
            path = tmp_path / "source.duckdb"
            admin = ibis.duckdb.connect(path)
            try:
                for name, facts in zip(
                    ("subjects", "left_facts", "right_facts"), datasets, strict=True
                ):
                    admin.raw_sql(f"CREATE TABLE {name} ({facts.columns})")
                    admin.raw_sql(f"INSERT INTO {name} VALUES {facts.values}")
            finally:
                admin.disconnect()
            connection = stack.enter_context(
                provider_for(backend).open(
                    datasource(backend, {"path": str(path), "read_only": True})
                )
            )
            cases = [
                Case(connection, TableSourceIR(name), {"backend": backend, "read_only": True})
                for name in ("subjects", "left_facts", "right_facts")
            ]
        else:
            cases = [
                stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, facts))
                for facts in datasets
            ]
        subject = cases[0]
        arguments = {
            **subject.session.datasource.fields,
            **{key + "_env": value for key, value in subject.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_R93_READER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_R93_READER"
        models = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, case in zip(("subjects", "left", "right"), cases, strict=True):
            assert isinstance(case.source, TableSourceIR)
            primary = ["owner", "tenant"] if name == "subjects" else ["id"]
            models += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key={primary!r})\n"
            for field in ("owner", "tenant"):
                models += f"{name}_{field}=ms.dimension_column(name={field!r},entity={name},column={field!r})\n"
            models += f"{name}_time=ms.time_dimension_column(name='time',entity={name},column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
            if name != "subjects":
                models += f"{name}_amount=ms.measure_column(name='amount',entity={name},column='amount',additivity=ms.additive_all())\n"
                models += f"{name}_total=ms.aggregate(name={name + '_total'!r},measure={name}_amount,agg='sum',empty=ms.empty.zero())\n"
                models += f"{name}_subject=ms.relationship(name={name + '_subject'!r},from_entity={name},to_entity=subjects,keys=[ms.join_on({name}_owner,subjects_owner),ms.join_on({name}_tenant,subjects_tenant)])\n"
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
        session = mv.session.get_or_create("r94-multiroot", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.subjects"))
        routes = mv.routes(
            mv.route(
                ms.ref.entity("sales.left"), through=(ms.ref.relationship("sales.left_subject"),)
            ),
            mv.route(
                ms.ref.entity("sales.right"), through=(ms.ref.relationship("sales.right_subject"),)
            ),
        )
        with monkeypatch.context() as construction:
            construction.setattr(SourceSession, "compile", forbidden)
            construction.setattr(SourceSession, "batches", forbidden)
            relations = {
                "linear": members.observe(
                    mv.runtime_metric.linear(
                        add=[ms.ref.metric("sales.left_total"), ms.ref.metric("sales.right_total")],
                        label="combined",
                    ),
                    via=routes,
                ),
                "ratio": members.observe(
                    mv.runtime_metric.ratio(
                        ms.ref.metric("sales.left_total"),
                        ms.ref.metric("sales.right_total"),
                        label="ratio",
                    ),
                    via=routes,
                ),
                "weighted": members.observe(
                    mv.runtime_metric.weighted_mean(
                        ms.ref.measure("sales.left.amount"),
                        ms.ref.measure("sales.left.amount"),
                        label="weighted",
                    ),
                    via=ms.ref.relationship("sales.left_subject"),
                ),
                "aggregate": members.observe(
                    mv.runtime_metric.aggregate(
                        ms.ref.measure("sales.left.amount"), agg="sum", label="total"
                    ),
                    via=ms.ref.relationship("sales.left_subject"),
                ),
                "slice": members.observe(
                    mv.runtime_metric.slice(
                        ms.ref.metric("sales.right_total"),
                        by={ms.ref.dimension("sales.right.owner"): "a"},
                        label="selected",
                    ),
                    via=ms.ref.relationship("sales.right_subject"),
                ),
            }
        originals: dict[str, Json] = {}
        parts: dict[str, Json] = {}
        for kind, relation in relations.items():
            result = relation.execute()
            assert isinstance(
                result, (mv.MaterializedNumericRelation, mv.MaterializedRatioRelation)
            )
            originals[kind], parts[kind] = snapshot(result), components(result)
            r93_source_trace.record(result)
        state: dict[str, Json] = {
            "session": session.id,
            "originals": originals,
            "components": parts,
        }
        (tmp_path / "r94-multiroot.json").write_bytes(encode(state))
        r93_source_trace.save(
            "multiroot-source-" + backend,
            {**subject.environment, "profile": profile},
            {"roots": [facts.values for facts in datasets]},
            {
                "independent_root_totals": [120, 29],
                "linear_total": 149,
                "ratio_numerator": 120,
                "ratio_denominator": 29,
                "weighted_numerator": 4600,
                "weighted_denominator": 120,
                "construction_business_reads_forbidden": True,
            },
            None,
            tuple(case.session for case in cases),
        )
    shutil.rmtree(tmp_path / "models")
    for pattern in ("source.duckdb*", "source.sqlite*"):
        for path in tmp_path.glob(pattern):
            path.unlink()
    reports: list[Json] = []
    repository = Path(__file__).resolve().parents[1]
    for phase in ("fixed", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.r94_multiroot_recovery_worker",
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
        Path(destination, "multiroot-recovery-" + backend + ".json").write_bytes(
            encode(
                {"backend": backend, "source_pid": os.getpid(), "source": state, "reports": reports}
            )
        )
