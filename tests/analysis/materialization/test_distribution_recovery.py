"""One native setup binds all licensed C10 distributions to offline recovery."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import NoReturn

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.distribution_fixtures import distribution_data
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import Case, datasource, source_case
from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_native_distributions_independent_fixed_and_cold(
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
    datasets = [
        distribution_data(backend, [(1, "a", 0), (2, "b", 0), (3, "c", 0)]),
        distribution_data(
            backend,
            [
                (9007199254740992, "a", 2),
                (9007199254740993, "a", 6),
                (9007199254740994, "a", 6),
                (9007199254740995, "a", None),
                (9007199254740993, "b", 6),
            ],
        ),
    ]
    with ExitStack() as stack:
        if backend == "duckdb":
            path = tmp_path / "source.duckdb"
            admin = ibis.duckdb.connect(path)
            for name, data in zip(("subjects", "facts"), datasets, strict=True):
                admin.raw_sql(f"CREATE TABLE {name} ({data.columns})")
                admin.raw_sql(f"INSERT INTO {name} VALUES {data.values}")
            admin.disconnect()
            connection = stack.enter_context(
                provider_for(backend).open(
                    datasource(backend, {"path": str(path), "read_only": True})
                )
            )
            subject, facts = [
                Case(connection, TableSourceIR(name), {"backend": backend, "read_only": True})
                for name in ("subjects", "facts")
            ]
        else:
            subject, facts = [
                stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
                for data in datasets
            ]
        args = {
            **subject.session.datasource.fields,
            **{key + "_env": value for key, value in subject.session.datasource.env_refs.items()},
        }
        if "user" in args:
            monkeypatch.setenv("MARIVO_R93_READER", str(args.pop("user")))
            args["user_env"] = "MARIVO_R93_READER"
        models = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, case in (("subjects", subject), ("facts", facts)):
            assert isinstance(case.source, TableSourceIR)
            keys = ["owner"] if name == "subjects" else ["id", "owner"]
            models += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key={keys!r})\n"
            models += (
                f"{name}_owner=ms.dimension_column(name='owner',entity={name},column='owner')\n"
            )
            models += f"{name}_time=ms.time_dimension_column(name='time',entity={name},column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
        models += "identity=ms.measure_column(name='identity',entity=facts,column='id',additivity=ms.additive_all())\n"
        models += "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
        models += "facts_subject=ms.relationship(name='facts_subject',from_entity=facts,to_entity=subjects,keys=[ms.join_on(facts_owner,subjects_owner)])\n"
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in args.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                "sales/models.py": models,
            }
        )
        if backend not in ("duckdb", "sqlite"):

            def forbid_staging(*args: object, **kwargs: object) -> NoReturn:
                pytest.fail("C10 cannot create or drop backend tables")

            monkeypatch.setattr(type(subject.session._backend), "create_table", forbid_staging)
            monkeypatch.setattr(type(subject.session._backend), "drop_table", forbid_staging)
        session = mv.session.get_or_create("r94-distribution", report_timezone="UTC")
        originals: dict[str, Json] = {}
        kinds = ["approx_distinct"]
        if backend != "clickhouse":
            kinds += ["distinct", "identity"]
        if backend in ("duckdb", "postgres"):
            kinds += ["quantile"]
        if backend not in ("sqlite", "mysql"):
            kinds += ["approx_quantile"]
        for kind in kinds:
            metric = mv.runtime_metric.aggregate(
                ms.ref.measure(
                    "sales.facts.identity" if kind == "identity" else "sales.facts.amount"
                ),
                agg="count_distinct"
                if kind in ("distinct", "identity")
                else "approx_count_distinct"
                if kind == "approx_distinct"
                else ("percentile", 0.25)
                if kind == "quantile"
                else ("approx_percentile", 0.25),
                label=kind,
            )
            value = (
                session.members(ms.ref.entity("sales.subjects"))
                .observe(
                    metric,
                    during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                    via=ms.ref.relationship("sales.facts_subject"),
                    by=(mv.member(),),
                )
                .execute()
            )
            assert isinstance(value, mv.MaterializedNumericRelation)
            frame = value.to_pandas().set_index("member")
            if kind in ("distinct", "identity", "approx_distinct"):
                assert frame.value.to_dict() == {
                    "a": 4 if kind == "identity" else 2,
                    "b": 1,
                    "c": 0,
                }
            else:
                assert frame.loc["b", "value"] == 6 and frame.loc["c", "cell_tag"] == "null"
                if kind == "quantile":
                    assert frame.loc["a", "value"] == 4
                else:
                    number = frame.loc["a", "value"]
                    assert isinstance(number, (int, float)) and 2 <= number <= 6
            originals[kind] = snapshot(value)
            source_trace.save(
                "distribution-source-" + backend + "-" + kind,
                {**subject.environment, "profile": profile},
                {"facts": datasets[1].values, "identity_types": ["int64", "string"]},
                {
                    "kind": kind,
                    "empty_cell": "null" if "quantile" in kind else "defined",
                    "a_exact": 4
                    if kind in ("identity", "quantile")
                    else 2
                    if "quantile" not in kind
                    else None,
                },
                value,
                (subject.session, facts.session),
            )
        state: dict[str, Json] = {"session": session.id, "originals": originals}
        (tmp_path / "r94-distribution.json").write_bytes(encode(state))
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
                "tests.analysis.materialization.distribution_recovery_worker",
                str(tmp_path),
                phase,
                str(output),
            ],
            cwd=repository,
            env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "distribution-recovery-" + backend + ".json").write_bytes(
            encode(
                {"backend": backend, "source_pid": os.getpid(), "source": state, "reports": reports}
            )
        )
